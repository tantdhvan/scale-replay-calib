#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <numeric>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <unordered_set>
#include <utility>
#include <vector>

#if defined(_WIN32)
#include <windows.h>
#include <psapi.h>
#elif defined(__linux__)
#include <unistd.h>
#endif

using Real = long double;
using Clock = std::chrono::steady_clock;

struct Graph {
    int n = 0;
    std::vector<std::pair<int, int>> edges;
    std::vector<std::vector<int>> neighbors;
};

struct Resources {
    std::vector<Real> mu;
    std::vector<Real> variance;
};

struct Candidate {
    std::vector<int> items;
    std::unordered_set<int> members;
    Real value = 0;
    Real mu = 0;
    Real variance = 0;
};

struct Result {
    std::string algorithm;
    Candidate solution;
    std::uint64_t queries = 0;
    std::size_t activeStates = 0;
    std::size_t retainedSlots = 0;
    double milliseconds = 0;
    double peakMemoryMb = 0;
};

double peakMemoryMb() {
#if defined(_WIN32)
    PROCESS_MEMORY_COUNTERS counters{};
    if (GetProcessMemoryInfo(GetCurrentProcess(), &counters, sizeof(counters)))
        return static_cast<double>(counters.PeakWorkingSetSize) / (1024.0 * 1024.0);
#elif defined(__linux__)
    std::ifstream status("/proc/self/status");
    std::string key;
    std::size_t value = 0;
    std::string unit;
    while (status >> key >> value >> unit) {
        if (key == "VmHWM:")
            return static_cast<double>(value) / 1024.0;
    }
#endif
    return 0.0;
}

struct Config {
    std::string graphPath;
    std::string order = "random";
    std::string mode = "all";
    Real budget = 0;
    Real budgetFraction = 0;
    Real alpha = 0.05L;
    Real epsilon = 0.053L;
    Real delta = 0.053L;
    Real gamma = 0.053L;
    std::string configuration = "theory";
    std::uint64_t orderSeed = 45;
    bool smoke = false;
};

Real normalQuantile(Real alpha) {
    if (!(alpha > 0 && alpha < 0.5L))
        throw std::runtime_error("alpha must lie in (0, 0.5)");
    const Real target = 1 - alpha;
    const Real invSqrt2 = 1 / std::sqrt(Real(2));
    Real lo = -10, hi = 10;
    for (int i = 0; i < 120; ++i) {
        Real mid = (lo + hi) / 2;
        Real cdf = 0.5L * std::erfc(-mid * invSqrt2);
        if (cdf < target)
            lo = mid;
        else
            hi = mid;
    }
    return (lo + hi) / 2;
}

Graph readGraph(const std::string &path, Resources &resources) {
    std::ifstream in(path);
    if (!in)
        throw std::runtime_error("cannot open graph: " + path);
    Graph graph;
    int m = 0;
    in >> graph.n >> m;
    if (!in || graph.n <= 0 || m < 0)
        throw std::runtime_error("invalid graph header");
    graph.edges.reserve(static_cast<std::size_t>(m));
    graph.neighbors.assign(static_cast<std::size_t>(graph.n), {});
    for (int i = 0; i < m; ++i) {
        int u = 0, v = 0;
        in >> u >> v;
        if (!in || u < 0 || v < 0 || u >= graph.n || v >= graph.n || u == v)
            throw std::runtime_error("invalid edge at index " + std::to_string(i));
        graph.edges.emplace_back(u, v);
        graph.neighbors[static_cast<std::size_t>(u)].push_back(v);
        graph.neighbors[static_cast<std::size_t>(v)].push_back(u);
    }
    std::string marker;
    int count = 0;
    in >> marker >> count;
    if (marker != "VERTEX_GAUSSIAN_COSTS_P0_V1" || count != graph.n)
        throw std::runtime_error("missing P0 Gaussian resource block");
    resources.mu.assign(static_cast<std::size_t>(graph.n), 0);
    resources.variance.assign(static_cast<std::size_t>(graph.n), 0);
    for (int i = 0; i < graph.n; ++i) {
        int vertex = 0;
        Real mu = 0, variance = 0;
        in >> vertex >> mu >> variance;
        if (!in || vertex < 0 || vertex >= graph.n || mu < 0 || variance <= 0)
            throw std::runtime_error("invalid Gaussian parameters");
        resources.mu[static_cast<std::size_t>(vertex)] = mu;
        resources.variance[static_cast<std::size_t>(vertex)] = variance;
    }
    return graph;
}

Real chanceScore(const Candidate &candidate, Real z) {
    return candidate.mu + z * std::sqrt(std::max(Real(0), candidate.variance));
}

bool feasible(const Candidate &candidate, Real z, Real budget) {
    Real tolerance = 128 * std::numeric_limits<Real>::epsilon() * std::max(Real(1), budget);
    return chanceScore(candidate, z) <= budget + tolerance;
}

Real tangentCost(const Candidate &candidate, Real u, Real z, Real budget) {
    Real p = candidate.mu / budget;
    Real w = z * z * candidate.variance / (budget * budget);
    return (p + w / (2 * u)) / (1 - u / 2);
}

Real tangentItemCost(int vertex, Real u, const Resources &resources, Real z, Real budget) {
    Candidate singleton;
    singleton.mu = resources.mu[static_cast<std::size_t>(vertex)];
    singleton.variance = resources.variance[static_cast<std::size_t>(vertex)];
    return tangentCost(singleton, u, z, budget);
}

Candidate singletonCandidate(int vertex, Real value, const Resources &resources) {
    Candidate candidate;
    candidate.items.push_back(vertex);
    candidate.members.insert(vertex);
    candidate.value = value;
    candidate.mu = resources.mu[static_cast<std::size_t>(vertex)];
    candidate.variance = resources.variance[static_cast<std::size_t>(vertex)];
    return candidate;
}

class MaxCutOracle {
public:
    const Graph &graph;
    std::uint64_t queries = 0;

    explicit MaxCutOracle(const Graph &graph_) : graph(graph_) {}

    Real singleton(int vertex) {
        ++queries;
        return static_cast<Real>(graph.neighbors[static_cast<std::size_t>(vertex)].size());
    }

    Real marginal(const Candidate &candidate, int vertex) {
        ++queries;
        if (candidate.members.find(vertex) != candidate.members.end())
            return 0;
        Real delta = 0;
        for (int neighbor : graph.neighbors[static_cast<std::size_t>(vertex)]) {
            bool selected = candidate.members.find(neighbor) != candidate.members.end();
            delta += selected ? -1 : 1;
        }
        return delta;
    }
};

void addKnown(Candidate &candidate, int vertex, Real marginal, const Resources &resources) {
    if (candidate.members.insert(vertex).second) {
        candidate.items.push_back(vertex);
        candidate.value += marginal;
        candidate.mu += resources.mu[static_cast<std::size_t>(vertex)];
        candidate.variance += resources.variance[static_cast<std::size_t>(vertex)];
    }
}

Candidate withKnownAddition(const Candidate &base, int vertex, Real value, const Resources &resources) {
    Candidate result = base;
    Real marginal = value - base.value;
    addKnown(result, vertex, marginal, resources);
    result.value = value;
    return result;
}

void keepBest(Candidate &best, const Candidate &candidate) {
    if (candidate.value > best.value + 1e-12L)
        best = candidate;
}

std::vector<Real> tangentGrid(Real epsilon) {
    int m = static_cast<int>(std::ceil(2 / std::sqrt(epsilon)));
    std::vector<Real> grid;
    grid.reserve(static_cast<std::size_t>(m));
    for (int j = 1; j <= m; ++j) {
        Real x = static_cast<Real>(j) / static_cast<Real>(m);
        grid.push_back(x * x);
    }
    return grid;
}

std::vector<int> makeOrder(const Graph &graph, const Resources &resources,
                           const std::string &kind, std::uint64_t seed) {
    std::vector<int> order(static_cast<std::size_t>(graph.n));
    std::iota(order.begin(), order.end(), 0);
    if (kind == "random") {
        std::mt19937_64 rng(seed);
        std::shuffle(order.begin(), order.end(), rng);
    } else if (kind == "mu_asc") {
        std::stable_sort(order.begin(), order.end(), [&](int a, int b) {
            return resources.mu[static_cast<std::size_t>(a)] < resources.mu[static_cast<std::size_t>(b)];
        });
    } else if (kind == "mu_desc") {
        std::stable_sort(order.begin(), order.end(), [&](int a, int b) {
            return resources.mu[static_cast<std::size_t>(a)] > resources.mu[static_cast<std::size_t>(b)];
        });
    } else if (kind != "file") {
        throw std::runtime_error("order must be file, random, mu_asc, or mu_desc");
    }
    return order;
}

struct State3 {
    int tangent = 0;
    int exponent = 0;
    Real scale = 0;
    std::vector<Candidate> candidates;
    State3() = default;
    State3(int tangent_, int exponent_, Real scale_)
        : tangent(tangent_), exponent(exponent_), scale(scale_), candidates(3) {}
};

struct State5 {
    int tangent = 0;
    int exponent = 0;
    Real scale = 0;
    std::vector<Candidate> light;
    std::vector<Candidate> heavy;
    State5() = default;
    State5(int tangent_, int exponent_, Real scale_)
        : tangent(tangent_), exponent(exponent_), scale(scale_), light(3), heavy(2) {}
};

template <typename State>
std::size_t itemSlots(const std::map<std::pair<int, int>, State> &states) {
    std::size_t slots = 0;
    for (const auto &entry : states) {
        const State &state = entry.second;
        if constexpr (std::is_same_v<State, State3>) {
            for (const Candidate &candidate : state.candidates)
                slots += candidate.items.size();
        } else {
            for (const Candidate &candidate : state.light)
                slots += candidate.items.size();
            for (const Candidate &candidate : state.heavy)
                slots += candidate.items.size();
        }
    }
    return slots;
}

template <typename State>
void eraseBelow(std::map<std::pair<int, int>, State> &states, Real lower) {
    for (auto it = states.begin(); it != states.end();) {
        if (it->second.scale < lower * (1 - 1e-15L))
            it = states.erase(it);
        else
            ++it;
    }
}

template <typename State>
void syncStates(std::map<std::pair<int, int>, State> &states,
                const std::vector<Real> &tangents, Real a0, Real lower,
                Real upper, Real growth) {
    if (!(a0 > 0) || upper < lower)
        return;
    for (int k = 0;; ++k) {
        Real scale = a0 * std::pow(growth, static_cast<Real>(k));
        if (scale > upper * (1 + 1e-15L))
            break;
        if (scale + 1e-15L < lower)
            continue;
        for (int tangent = 0; tangent < static_cast<int>(tangents.size()); ++tangent) {
            auto key = std::make_pair(tangent, k);
            if (states.find(key) == states.end())
                states.emplace(key, State(tangent, k, scale));
        }
    }
    eraseBelow(states, lower);
}

Result runOnePass(const Graph &graph, const Resources &resources, const std::vector<int> &order,
                Real z, Real budget, Real epsilon, Real delta, Real gamma) {
    auto started = Clock::now();
    MaxCutOracle oracle(graph);
    std::vector<Real> tangents = tangentGrid(epsilon);
    std::map<std::pair<int, int>, State3> states;
    Candidate bestSingleton;
    Real a0 = 0, maximum = 0, total = 0;
    for (int vertex : order) {
        Real value = oracle.singleton(vertex);
        Candidate singleton = singletonCandidate(vertex, value, resources);
        bool allowed = feasible(singleton, z, budget);
        if (allowed) {
            keepBest(bestSingleton, singleton);
            maximum = std::max(maximum, value);
            total += value;
            if (!(a0 > 0) && value > 0)
                a0 = value;
        }
        if (a0 > 0)
            syncStates(states, tangents, a0, maximum / (1 + delta), total / gamma, 1 + delta);
        for (auto &entry : states) {
            State3 &state = entry.second;
            Real u = tangents[static_cast<std::size_t>(state.tangent)];
            Real cost = tangentItemCost(vertex, u, resources, z, budget);
            std::vector<Real> marginals(3);
            for (int i = 0; i < 3; ++i)
                marginals[static_cast<std::size_t>(i)] = oracle.marginal(state.candidates[static_cast<std::size_t>(i)], vertex);
            int chosen = 0;
            for (int i = 1; i < 3; ++i)
                if (marginals[static_cast<std::size_t>(i)] > marginals[static_cast<std::size_t>(chosen)])
                    chosen = i;
            Real rho = 2 * (1 - gamma) * state.scale / (Real(15) / 2 + 3 * epsilon);
            if (allowed && marginals[static_cast<std::size_t>(chosen)] >= rho * cost &&
                tangentCost(state.candidates[static_cast<std::size_t>(chosen)], u, z, budget) + cost <= 1 + 1e-15L)
                addKnown(state.candidates[static_cast<std::size_t>(chosen)], vertex,
                         marginals[static_cast<std::size_t>(chosen)], resources);
        }
    }
    Candidate best = bestSingleton;
    for (const auto &entry : states)
        for (const Candidate &candidate : entry.second.candidates)
            keepBest(best, candidate);
    auto finished = Clock::now();
    Result result;
    result.algorithm = "1P-NSMGC";
    result.solution = best;
    result.queries = oracle.queries;
    result.activeStates = states.size();
    result.retainedSlots = itemSlots(states);
    result.milliseconds = std::chrono::duration<double, std::milli>(finished - started).count();
    result.peakMemoryMb = peakMemoryMb();
    return result;
}

Result runTwoPass(const Graph &graph, const Resources &resources, const std::vector<int> &order,
                 const std::vector<int> &replayOrder, const std::vector<Real> &tangents,
                 Real z, Real budget, Real epsilon, Real delta, Real gamma) {
    auto started = Clock::now();
    MaxCutOracle oracle(graph);
    std::map<std::pair<int, int>, State5> states;
    Candidate bestSingleton;
    Real a0 = 0, maximum = 0, total = 0;
    Real beta = 1 / (6 + 6 * epsilon);
    for (int vertex : order) {
        Real value = oracle.singleton(vertex);
        Candidate singleton = singletonCandidate(vertex, value, resources);
        bool allowed = feasible(singleton, z, budget);
        if (allowed) {
            keepBest(bestSingleton, singleton);
            maximum = std::max(maximum, value);
            total += value;
            if (!(a0 > 0) && value > 0)
                a0 = value;
        }
        if (a0 > 0)
            syncStates(states, tangents, a0, maximum / (1 + delta), 4 * total / gamma, 1 + delta);
        for (auto &entry : states) {
            State5 &state = entry.second;
            Real u = tangents[static_cast<std::size_t>(state.tangent)];
            Real cost = tangentItemCost(vertex, u, resources, z, budget);
            std::vector<Real> lightMarginals(3), heavyMarginals(2);
            for (int i = 0; i < 3; ++i)
                lightMarginals[static_cast<std::size_t>(i)] = oracle.marginal(state.light[static_cast<std::size_t>(i)], vertex);
            for (int i = 0; i < 2; ++i)
                heavyMarginals[static_cast<std::size_t>(i)] = oracle.marginal(state.heavy[static_cast<std::size_t>(i)], vertex);
            int li = 0;
            for (int i = 1; i < 3; ++i)
                if (lightMarginals[static_cast<std::size_t>(i)] > lightMarginals[static_cast<std::size_t>(li)]) li = i;
            int hi = heavyMarginals[1] > heavyMarginals[0] ? 1 : 0;
            Real rhoA = beta * (1 - gamma) * state.scale;
            Real rhoH = 2 * beta * (1 - gamma) * state.scale;
            if (allowed && lightMarginals[static_cast<std::size_t>(li)] >= rhoA * cost &&
                tangentCost(state.light[static_cast<std::size_t>(li)], u, z, budget) + cost <= 1 + 1e-15L)
                addKnown(state.light[static_cast<std::size_t>(li)], vertex, lightMarginals[static_cast<std::size_t>(li)], resources);
            if (allowed && heavyMarginals[static_cast<std::size_t>(hi)] >= rhoH * cost &&
                tangentCost(state.heavy[static_cast<std::size_t>(hi)], u, z, budget) + cost <= 1 + 1e-15L)
                addKnown(state.heavy[static_cast<std::size_t>(hi)], vertex, heavyMarginals[static_cast<std::size_t>(hi)], resources);
        }
    }
    Candidate best = bestSingleton;
    for (const auto &entry : states) {
        for (const Candidate &candidate : entry.second.light) keepBest(best, candidate);
        for (const Candidate &candidate : entry.second.heavy) keepBest(best, candidate);
    }
    for (int vertex : replayOrder) {
        Candidate bestAugmentation;
        for (auto &entry : states) {
            State5 &state = entry.second;
            Real u = tangents[static_cast<std::size_t>(state.tangent)];
            Real cost = tangentItemCost(vertex, u, resources, z, budget);
            for (Candidate &base : state.light) {
                if (!feasible(singletonCandidate(vertex, 0, resources), z, budget)) continue;
                if (tangentCost(base, u, z, budget) + cost > 1 + 1e-15L) continue;
                Real value = oracle.marginal(base, vertex) + base.value;
                Candidate augmented = withKnownAddition(base, vertex, value, resources);
                keepBest(best, augmented);
            }
        }
    }
    auto finished = Clock::now();
    Result result;
    result.algorithm = "2P-NSMGC";
    result.solution = best;
    result.queries = oracle.queries;
    result.activeStates = states.size();
    result.retainedSlots = itemSlots(states);
    result.milliseconds = std::chrono::duration<double, std::milli>(finished - started).count();
    result.peakMemoryMb = peakMemoryMb();
    return result;
}

Candidate runCalibrationPass(const Graph &graph, const Resources &resources, const std::vector<int> &order,
                             Real z, Real budget, std::uint64_t &queries) {
    MaxCutOracle oracle(graph);
    std::map<int, State3> states;
    Candidate bestSingleton;
    Real a0 = 0, maximum = 0, total = 0;
    const Real u = 0.5L, growth = 1.5L, gamma0 = 0.5L;
    for (int vertex : order) {
        Real value = oracle.singleton(vertex);
        Candidate singleton = singletonCandidate(vertex, value, resources);
        bool allowed = feasible(singleton, z, budget);
        if (allowed) {
            keepBest(bestSingleton, singleton);
            maximum = std::max(maximum, value);
            total += value;
            if (!(a0 > 0) && value > 0) a0 = value;
        }
        if (a0 > 0) {
            Real lower = maximum / 1.5L, upper = total / gamma0;
            for (int k = 0;; ++k) {
                Real scale = a0 * std::pow(growth, static_cast<Real>(k));
                if (scale > upper * (1 + 1e-15L)) break;
                if (scale + 1e-15L < lower) continue;
                if (states.find(k) == states.end()) states.emplace(k, State3(0, k, scale));
            }
            for (auto it = states.begin(); it != states.end();) {
                if (it->second.scale < lower * (1 - 1e-15L)) it = states.erase(it);
                else ++it;
            }
        }
        for (auto &entry : states) {
            State3 &state = entry.second;
            Real cost = tangentItemCost(vertex, u, resources, z, budget);
            std::vector<Real> marginals(3);
            for (int i = 0; i < 3; ++i) marginals[static_cast<std::size_t>(i)] = oracle.marginal(state.candidates[static_cast<std::size_t>(i)], vertex);
            int chosen = marginals[1] > marginals[0] ? 1 : 0;
            if (marginals[2] > marginals[static_cast<std::size_t>(chosen)]) chosen = 2;
            if (allowed && marginals[static_cast<std::size_t>(chosen)] >= (2 * state.scale / 17) * cost &&
                tangentCost(state.candidates[static_cast<std::size_t>(chosen)], u, z, budget) + cost <= 1 + 1e-15L)
                addKnown(state.candidates[static_cast<std::size_t>(chosen)], vertex, marginals[static_cast<std::size_t>(chosen)], resources);
        }
    }
    Candidate best = bestSingleton;
    for (const auto &entry : states)
        for (const Candidate &candidate : entry.second.candidates) keepBest(best, candidate);
    queries = oracle.queries;
    return best;
}

Result runThreePass(const Graph &graph, const Resources &resources, const std::vector<int> &order,
                const std::vector<int> &replayOrder, Real z, Real budget,
                Real epsilon, Real delta) {
    auto started = Clock::now();
    std::uint64_t calibrationQueries = 0;
    Candidate gammaCandidate = runCalibrationPass(graph, resources, order, z, budget, calibrationQueries);
    if (!(gammaCandidate.value > 0)) {
        Result result;
        result.algorithm = "3P-NSMGC";
        result.solution = Candidate{};
        result.queries = calibrationQueries;
        result.milliseconds = std::chrono::duration<double, std::milli>(Clock::now() - started).count();
        return result;
    }
    std::vector<Real> tangents = tangentGrid(epsilon);
    std::vector<Real> scales;
    for (int k = 0;; ++k) {
        Real scale = gammaCandidate.value * std::pow(1 + delta, static_cast<Real>(k));
        if (scale > 102 * gammaCandidate.value * (1 + 1e-15L)) break;
        scales.push_back(scale);
    }
    std::vector<State5> states;
    for (int ti = 0; ti < static_cast<int>(tangents.size()); ++ti)
        for (int si = 0; si < static_cast<int>(scales.size()); ++si)
            states.emplace_back(ti, si, scales[static_cast<std::size_t>(si)]);
    MaxCutOracle oracle(graph);
    Candidate best = gammaCandidate;
    const Real beta = 1 / (6 + 6 * epsilon);
    for (int vertex : order) {
        Candidate singleton = singletonCandidate(vertex, oracle.singleton(vertex), resources);
        bool allowed = feasible(singleton, z, budget);
        if (allowed) keepBest(best, singleton);
        for (State5 &state : states) {
            Real u = tangents[static_cast<std::size_t>(state.tangent)];
            Real cost = tangentItemCost(vertex, u, resources, z, budget);
            std::vector<Real> lm(3), hm(2);
            for (int i = 0; i < 3; ++i) lm[static_cast<std::size_t>(i)] = oracle.marginal(state.light[static_cast<std::size_t>(i)], vertex);
            for (int i = 0; i < 2; ++i) hm[static_cast<std::size_t>(i)] = oracle.marginal(state.heavy[static_cast<std::size_t>(i)], vertex);
            int li = lm[1] > lm[0] ? 1 : 0;
            if (lm[2] > lm[static_cast<std::size_t>(li)]) li = 2;
            int hi = hm[1] > hm[0] ? 1 : 0;
            if (allowed && lm[static_cast<std::size_t>(li)] >= beta * state.scale * cost &&
                tangentCost(state.light[static_cast<std::size_t>(li)], u, z, budget) + cost <= 1 + 1e-15L)
                addKnown(state.light[static_cast<std::size_t>(li)], vertex, lm[static_cast<std::size_t>(li)], resources);
            if (allowed && hm[static_cast<std::size_t>(hi)] >= 2 * beta * state.scale * cost &&
                tangentCost(state.heavy[static_cast<std::size_t>(hi)], u, z, budget) + cost <= 1 + 1e-15L)
                addKnown(state.heavy[static_cast<std::size_t>(hi)], vertex, hm[static_cast<std::size_t>(hi)], resources);
        }
    }
    for (const State5 &state : states) {
        for (const Candidate &candidate : state.light) keepBest(best, candidate);
        for (const Candidate &candidate : state.heavy) keepBest(best, candidate);
    }
    for (int vertex : replayOrder) {
        for (State5 &state : states) {
            Real u = tangents[static_cast<std::size_t>(state.tangent)];
            Real cost = tangentItemCost(vertex, u, resources, z, budget);
            Candidate singleton = singletonCandidate(vertex, 0, resources);
            if (!feasible(singleton, z, budget)) continue;
            for (Candidate &base : state.light) {
                if (tangentCost(base, u, z, budget) + cost > 1 + 1e-15L) continue;
                Real value = base.value + oracle.marginal(base, vertex);
                keepBest(best, withKnownAddition(base, vertex, value, resources));
            }
        }
    }
    auto finished = Clock::now();
    Result result;
    result.algorithm = "3P-NSMGC";
    result.solution = best;
    result.queries = calibrationQueries + oracle.queries;
    result.activeStates = states.size();
    std::size_t slots = 0;
    for (const State5 &state : states) {
        for (const Candidate &candidate : state.light) slots += candidate.items.size();
        for (const Candidate &candidate : state.heavy) slots += candidate.items.size();
    }
    result.retainedSlots = slots;
    result.milliseconds = std::chrono::duration<double, std::milli>(finished - started).count();
    result.peakMemoryMb = peakMemoryMb();
    return result;
}

Result runOfflineGreedy(const Graph &graph, const Resources &resources, const std::vector<int> &order,
                        Real z, Real budget) {
    auto started = Clock::now();
    MaxCutOracle oracle(graph);
    Candidate best;
    for (int vertex : order) {
        Candidate singleton = singletonCandidate(vertex, oracle.singleton(vertex), resources);
        if (feasible(singleton, z, budget)) keepBest(best, singleton);
    }
    Candidate current;
    std::vector<char> used(static_cast<std::size_t>(graph.n), false);
    while (true) {
        int chosen = -1;
        Real chosenDensity = 0;
        Real chosenMarginal = 0;
        for (int vertex : order) {
            if (used[static_cast<std::size_t>(vertex)]) continue;
            Candidate singleton = singletonCandidate(vertex, 0, resources);
            Candidate next = current;
            Real marginal = oracle.marginal(current, vertex);
            addKnown(next, vertex, marginal, resources);
            if (!feasible(next, z, budget) || marginal <= 0) continue;
            Real cost = chanceScore(next, z) - chanceScore(current, z);
            if (!(cost > 0)) cost = std::numeric_limits<Real>::epsilon();
            Real density = marginal / cost;
            if (chosen < 0 || density > chosenDensity + 1e-18L ||
                (std::abs(density - chosenDensity) <= 1e-18L && vertex < chosen)) {
                chosen = vertex;
                chosenDensity = density;
                chosenMarginal = marginal;
            }
        }
        if (chosen < 0) break;
        addKnown(current, chosen, chosenMarginal, resources);
        used[static_cast<std::size_t>(chosen)] = true;
        keepBest(best, current);
    }
    auto finished = Clock::now();
    Result result;
    result.algorithm = "Offline-Greedy";
    result.solution = best;
    result.queries = oracle.queries;
    result.retainedSlots = best.items.size();
    result.milliseconds = std::chrono::duration<double, std::milli>(finished - started).count();
    result.peakMemoryMb = peakMemoryMb();
    return result;
}

void printHeader() {
    std::cout << "algorithm,budget_fraction,B,objective,queries,feasible,chance_score,chance_cost_over_B,size,n,edges,epsilon,alpha,order,order_seed,tangents,active_states,retained_candidate_slots,delta,gamma,configuration,runtime_ms,peak_memory_mb\n";
}

void printResult(const Result &result, const Config &config, const Graph &graph,
                 Real z, Real tangentCount) {
    Real score = chanceScore(result.solution, z);
    bool ok = feasible(result.solution, z, config.budget);
    std::cout << std::setprecision(12)
              << result.algorithm << ',' << static_cast<double>(config.budgetFraction) << ','
              << static_cast<double>(config.budget) << ',' << static_cast<double>(result.solution.value) << ','
              << result.queries << ',' << (ok ? 1 : 0) << ',' << static_cast<double>(score) << ','
              << static_cast<double>(score / config.budget) << ',' << result.solution.items.size() << ','
              << graph.n << ',' << graph.edges.size() << ',' << static_cast<double>(config.epsilon) << ','
              << static_cast<double>(config.alpha) << ',' << config.order << ',' << config.orderSeed << ','
              << static_cast<std::size_t>(tangentCount) << ',' << result.activeStates << ','
              << result.retainedSlots << ',' << static_cast<double>(config.delta) << ','
              << static_cast<double>(config.gamma) << ',' << config.configuration << ','
              << result.milliseconds << ',' << result.peakMemoryMb << '\n';
}

Config parseArgs(int argc, char **argv) {
    Config config;
    auto take = [&](int &i) -> std::string {
        if (i + 1 >= argc) throw std::runtime_error("missing argument after " + std::string(argv[i]));
        return argv[++i];
    };
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--graph") config.graphPath = take(i);
        else if (arg == "--order") config.order = take(i);
        else if (arg == "--mode") config.mode = take(i);
        else if (arg == "--budget") config.budget = std::stold(take(i));
        else if (arg == "--budget-fraction") config.budgetFraction = std::stold(take(i));
        else if (arg == "--alpha") config.alpha = std::stold(take(i));
        else if (arg == "--epsilon") config.epsilon = std::stold(take(i));
        else if (arg == "--delta") config.delta = std::stold(take(i));
        else if (arg == "--gamma") config.gamma = std::stold(take(i));
        else if (arg == "--configuration") config.configuration = take(i);
        else if (arg == "--order-seed") config.orderSeed = std::stoull(take(i));
        else if (arg == "--smoke") config.smoke = true;
        else if (arg == "--help") {
            std::cout << "Usage: experiment --graph FILE --budget B [--budget-fraction R] "
                         "[--alpha .05] [--epsilon .053] [--delta .053] [--gamma .053] "
                         "[--configuration theory|practical-sparse] "
                         "[--order file|random|mu_asc|mu_desc] "
                         "[--order-seed 45] [--mode all|1P-NSMGC|2P-NSMGC|3P-NSMGC|offline]\n"
                         "       experiment --smoke\n";
            std::exit(0);
        } else {
            throw std::runtime_error("unknown argument: " + arg);
        }
    }
    if (!config.smoke && config.graphPath.empty()) throw std::runtime_error("--graph is required");
    if (!config.smoke && !(config.budget > 0)) throw std::runtime_error("--budget must be positive");
    if (!(config.epsilon > 0 && config.epsilon < 1.0 / 6.0)) throw std::runtime_error("epsilon must be in (0,1/6)");
    if (!(config.delta > 0)) throw std::runtime_error("delta must be positive");
    if (!(config.gamma > 0 && config.gamma < 1)) throw std::runtime_error("gamma must lie in (0,1)");
    const std::vector<std::string> allowedModes = {"all", "1P-NSMGC", "2P-NSMGC", "3P-NSMGC", "offline"};
    if (std::find(allowedModes.begin(), allowedModes.end(), config.mode) == allowedModes.end())
        throw std::runtime_error("--mode must be all, 1P-NSMGC, 2P-NSMGC, 3P-NSMGC, or offline");
    return config;
}

int smoke() {
    Graph graph;
    graph.n = 8;
    graph.edges = {{0,1},{1,2},{2,3},{3,4},{4,5},{5,6},{6,7},{7,0},{0,4},{1,5}};
    graph.neighbors.assign(8, {});
    for (auto [u, v] : graph.edges) {
        graph.neighbors[static_cast<std::size_t>(u)].push_back(v);
        graph.neighbors[static_cast<std::size_t>(v)].push_back(u);
    }
    Resources resources;
    resources.mu.assign(8, 1);
    resources.variance.assign(8, 0.25L);
    std::vector<int> order{0,1,2,3,4,5,6,7};
    Real z = normalQuantile(0.05L), budget = 5;
    auto grid = tangentGrid(0.053L);
    Config config;
    config.budget = budget;
    config.budgetFraction = 0.5L;
    config.alpha = 0.05L;
    config.epsilon = 0.053L;
    config.delta = 0.053L;
    config.gamma = 0.053L;
    config.configuration = "theory";
    config.order = "file";
    config.orderSeed = 0;
    printHeader();
    printResult(runOnePass(graph, resources, order, z, budget, config.epsilon, config.delta, config.gamma), config, graph, z, grid.size());
    printResult(runTwoPass(graph, resources, order, order, grid, z, budget, config.epsilon, config.delta, config.gamma), config, graph, z, grid.size());
    printResult(runThreePass(graph, resources, order, order, z, budget, config.epsilon, config.delta), config, graph, z, grid.size());
    printResult(runOfflineGreedy(graph, resources, order, z, budget), config, graph, z, grid.size());
    return 0;
}

int main(int argc, char **argv) {
    try {
        Config config = parseArgs(argc, argv);
        if (config.smoke) return smoke();
        Resources resources;
        Graph graph = readGraph(config.graphPath, resources);
        std::vector<int> order = makeOrder(graph, resources, config.order, config.orderSeed);
        Real z = normalQuantile(config.alpha);
        std::vector<Real> grid = tangentGrid(config.epsilon);
        printHeader();
        if (config.mode == "all" || config.mode == "1P-NSMGC")
            printResult(runOnePass(graph, resources, order, z, config.budget, config.epsilon, config.delta, config.gamma), config, graph, z, grid.size());
        if (config.mode == "all" || config.mode == "2P-NSMGC")
            printResult(runTwoPass(graph, resources, order, order, grid, z, config.budget, config.epsilon, config.delta, config.gamma), config, graph, z, grid.size());
        if (config.mode == "all" || config.mode == "3P-NSMGC")
            printResult(runThreePass(graph, resources, order, order, z, config.budget, config.epsilon, config.delta), config, graph, z, grid.size());
        if (config.mode == "all" || config.mode == "offline")
            printResult(runOfflineGreedy(graph, resources, order, z, config.budget), config, graph, z, grid.size());
    } catch (const std::exception &error) {
        std::cerr << "error: " << error.what() << '\n';
        return 1;
    }
    return 0;
}
