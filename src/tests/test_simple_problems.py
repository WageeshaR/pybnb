import os
import sys

import pytest

from pybnb.misc import infinity
from pybnb.solver import (SolverResults,
                          Solver)

thisdir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, thisdir)
try:
    from problems import (infeasible_max,
                          infeasible_min,
                          root_infeasible_max,
                          root_infeasible_min,
                          unbounded_max,
                          unbounded_min,
                          zero_objective_max,
                          zero_objective_min)
finally:
    sys.path.remove(thisdir)

_ignore_value_ = object()

# TODO: Test where we initialize the solver with the best_objective.
#       Highlighted an issue where we were using the initial trivial
#       lower bound from workers who had not seen any work yet, but
#       the real bound was reported by the single worker who had
#       received the root node. Was not a problem when there was only
#       one worker.

class TestProblems(object):

    def _execute_single_test(self,
                             problem,
                             baseline,
                             solver=None,
                             **kwds):
        if solver is None:
            solver = Solver(comm=None)
        orig = problem.new_node()
        problem.save_state(orig)
        results = solver.solve(problem, **kwds)

        current = problem.new_node()
        problem.save_state(current)
        assert len(current.state) == len(orig.state)
        for i in range(len(current.state)):
            assert current.state[i] == orig.state[i]
        assert len(vars(results)) > 0
        assert len(vars(results)) == len(vars(baseline))
        for name in sorted(sorted(list(vars(results).keys()))):
            if getattr(baseline, name) is _ignore_value_:
                continue
            assert getattr(results, name) == getattr(baseline, name), \
                ("value for '"+str(name)+"' ("+
                 str(getattr(results, name))+") does "
                 "not match baseline ("+
                 str(getattr(baseline, name))+")")
        assert solver.is_dispatcher
        if solver._disp.queue.qsize() > 0:
            q = solver.save_dispatcher_queue()
            assert len(q) == 2
            assert q.tree_id_labeler is not None
            assert len(q.states) == solver._disp.queue.qsize()
        else:
            assert solver.save_dispatcher_queue() is None

    def _execute_tests(self, problem, baseline, **kwds):
        assert 'log_interval_second' not in kwds
        assert 'log' not in kwds
        self._execute_single_test(problem, baseline, **kwds)
        kwds['log_interval_seconds'] = 0.0
        self._execute_single_test(problem, baseline, **kwds)
        kwds['log_interval_seconds'] = 100.0
        self._execute_single_test(problem, baseline, **kwds)
        kwds['log_interval_seconds'] = 0.0
        kwds['log'] = None
        self._execute_single_test(problem, baseline, **kwds)
        kwds['log_interval_seconds'] = 100.0
        self._execute_single_test(problem, baseline, **kwds)
        solver = Solver(comm=None)
        self._execute_single_test(problem, baseline, solver=solver, **kwds)
        self._execute_single_test(problem, baseline, solver=solver, **kwds)

    def test_infeasible_max(self):
        baseline = SolverResults()
        baseline.solution_status = "infeasible"
        baseline.termination_condition = "optimality"
        baseline.objective = -infinity
        baseline.bound = -infinity
        baseline.nodes = 255
        baseline.wall_time = _ignore_value_
        problem = infeasible_max.InfeasibleMax()
        self._execute_tests(problem, baseline)

        baseline = SolverResults()
        baseline.solution_status = "unknown"
        baseline.termination_condition = "no_nodes"
        baseline.objective = -infinity
        baseline.bound = -16
        baseline.nodes = 31
        baseline.wall_time = _ignore_value_
        problem = infeasible_max.InfeasibleMax(branching_abstol=0.1)
        self._execute_tests(problem, baseline)

        baseline = SolverResults()
        baseline.solution_status = "unknown"
        baseline.termination_condition = "cutoff"
        baseline.objective = -infinity
        baseline.bound = -16
        baseline.nodes = 31
        baseline.wall_time = _ignore_value_
        problem = infeasible_max.InfeasibleMax(branching_abstol=0.1)
        self._execute_tests(problem, baseline, cutoff=-15)

        baseline = SolverResults()
        baseline.solution_status = "feasible"
        baseline.termination_condition = "cutoff"
        baseline.objective = -20
        baseline.bound = -16
        baseline.absolute_gap = 4
        baseline.relative_gap = 0.2
        baseline.nodes = 31
        baseline.wall_time = _ignore_value_
        problem = infeasible_max.InfeasibleMax(branching_abstol=0.1,
                                               fixed_objective=-20)
        self._execute_tests(problem, baseline, cutoff=-15)


        baseline = SolverResults()
        baseline.solution_status = "unknown"
        baseline.termination_condition = "node_limit"
        baseline.objective = -infinity
        baseline.bound = -16
        baseline.nodes = 31
        baseline.wall_time = _ignore_value_
        problem = infeasible_max.InfeasibleMax()
        self._execute_tests(problem, baseline, node_limit=31)

        baseline = SolverResults()
        baseline.solution_status = "feasible"
        baseline.termination_condition = "node_limit"
        baseline.objective = -17
        baseline.bound = -16
        baseline.absolute_gap = 1.0
        baseline.relative_gap = 1.0/17
        baseline.nodes = 31
        baseline.wall_time = _ignore_value_
        problem = infeasible_max.InfeasibleMax(fixed_objective=-17)
        self._execute_tests(problem, baseline, node_limit=31)

        baseline = SolverResults()
        baseline.solution_status = "unknown"
        baseline.termination_condition = "time_limit"
        baseline.objective = -infinity
        baseline.bound = infinity
        baseline.nodes = 0
        baseline.wall_time = _ignore_value_
        problem = infeasible_max.InfeasibleMax()
        self._execute_tests(problem, baseline, time_limit=0)

    def test_infeasible_min(self):
        baseline = SolverResults()
        baseline.solution_status = "infeasible"
        baseline.termination_condition = "optimality"
        baseline.objective = infinity
        baseline.bound = infinity
        baseline.nodes = 255
        baseline.wall_time = _ignore_value_
        problem = infeasible_min.InfeasibleMin()
        self._execute_tests(problem, baseline)

        baseline = SolverResults()
        baseline.solution_status = "unknown"
        baseline.termination_condition = "no_nodes"
        baseline.objective = infinity
        baseline.bound = 16
        baseline.nodes = 31
        baseline.wall_time = _ignore_value_
        problem = infeasible_min.InfeasibleMin(branching_abstol=0.1)
        self._execute_tests(problem, baseline)

        baseline = SolverResults()
        baseline.solution_status = "unknown"
        baseline.termination_condition = "cutoff"
        baseline.objective = infinity
        baseline.bound = 16
        baseline.nodes = 31
        baseline.wall_time = _ignore_value_
        problem = infeasible_min.InfeasibleMin(branching_abstol=0.1)
        self._execute_tests(problem, baseline, cutoff=15)

        baseline = SolverResults()
        baseline.solution_status = "feasible"
        baseline.termination_condition = "cutoff"
        baseline.objective = 20
        baseline.bound = 16
        baseline.absolute_gap = 4
        baseline.relative_gap = 0.2
        baseline.nodes = 31
        baseline.wall_time = _ignore_value_
        problem = infeasible_min.InfeasibleMin(branching_abstol=0.1,
                                               fixed_objective=20)
        self._execute_tests(problem, baseline, cutoff=15)

        baseline = SolverResults()
        baseline.solution_status = "unknown"
        baseline.termination_condition = "node_limit"
        baseline.objective = infinity
        baseline.bound = 16
        baseline.nodes = 31
        baseline.wall_time = _ignore_value_
        problem = infeasible_min.InfeasibleMin()
        self._execute_tests(problem, baseline, node_limit=31)

        baseline = SolverResults()
        baseline.solution_status = "feasible"
        baseline.termination_condition = "node_limit"
        baseline.objective = 17
        baseline.bound = 16
        baseline.absolute_gap = 1
        baseline.relative_gap = 1.0/17
        baseline.nodes = 31
        baseline.wall_time = _ignore_value_
        problem = infeasible_min.InfeasibleMin(fixed_objective=17)
        self._execute_tests(problem, baseline, node_limit=31)

        baseline = SolverResults()
        baseline.solution_status = "unknown"
        baseline.termination_condition = "time_limit"
        baseline.objective = infinity
        baseline.bound = -infinity
        baseline.nodes = 0
        baseline.wall_time = _ignore_value_
        problem = infeasible_min.InfeasibleMin()
        self._execute_tests(problem, baseline, time_limit=0)

    def test_root_infeasible_max(self):
        baseline = SolverResults()
        baseline.solution_status = "infeasible"
        baseline.termination_condition = "optimality"
        baseline.objective = -infinity
        baseline.bound = -infinity
        baseline.nodes = 1
        baseline.wall_time = _ignore_value_
        problem = root_infeasible_max.RootInfeasibleMax()
        self._execute_tests(problem, baseline)

    def test_root_infeasible_min(self):
        baseline = SolverResults()
        baseline.solution_status = "infeasible"
        baseline.termination_condition = "optimality"
        baseline.objective = infinity
        baseline.bound = infinity
        baseline.nodes = 1
        baseline.wall_time = _ignore_value_
        problem = root_infeasible_min.RootInfeasibleMin()
        self._execute_tests(problem, baseline)

    def test_unbounded_max(self):
        baseline = SolverResults()
        baseline.solution_status = "unbounded"
        baseline.termination_condition = "no_nodes"
        baseline.objective = infinity
        baseline.bound = infinity
        baseline.nodes = 1
        baseline.wall_time = _ignore_value_
        problem = unbounded_max.UnboundedMax()
        self._execute_tests(problem, baseline)

    def test_unbounded_min(self):
        baseline = SolverResults()
        baseline.solution_status = "unbounded"
        baseline.termination_condition = "no_nodes"
        baseline.objective = -infinity
        baseline.bound = -infinity
        baseline.nodes = 1
        baseline.wall_time = _ignore_value_
        problem = unbounded_min.UnboundedMin()
        self._execute_tests(problem, baseline)

    def test_zero_objective_max(self):
        baseline = SolverResults()
        baseline.solution_status = "optimal"
        baseline.termination_condition = "optimality"
        baseline.objective = 0.0
        baseline.bound = 0.0078125
        baseline.absolute_gap = 0.0078125
        baseline.relative_gap = 0.0078125
        baseline.nodes = 255
        baseline.wall_time = _ignore_value_
        problem = zero_objective_max.ZeroObjectiveMax()
        self._execute_single_test(problem,
                                  baseline,
                                  relative_gap=0.01,
                                  absolute_gap=0.01)
        self._execute_single_test(problem,
                                  baseline,
                                  relative_gap=0.0,
                                  absolute_gap=0.01)
        self._execute_single_test(problem,
                                  baseline,
                                  relative_gap=0.01,
                                  absolute_gap=0.0)

        baseline = SolverResults()
        baseline.solution_status = "optimal"
        baseline.termination_condition = "optimality"
        baseline.objective = 0.0
        baseline.bound = 0.0009765625
        baseline.absolute_gap = 0.0009765625
        baseline.relative_gap = 0.0009765625
        baseline.nodes = 2047
        baseline.wall_time = _ignore_value_
        problem = zero_objective_max.ZeroObjectiveMax()
        self._execute_single_test(problem,
                                  baseline,
                                  relative_gap=0.001,
                                  absolute_gap=0.001)
        self._execute_single_test(problem,
                                  baseline,
                                  relative_gap=0.0,
                                  absolute_gap=0.001)
        self._execute_single_test(problem,
                                  baseline,
                                  relative_gap=0.001,
                                  absolute_gap=0.0)

        baseline = SolverResults()
        baseline.solution_status = "feasible"
        baseline.termination_condition = "no_nodes"
        baseline.objective = 0.0
        baseline.bound = 0.0009765625
        baseline.absolute_gap = 0.0009765625
        baseline.relative_gap = 0.0009765625
        baseline.nodes = 2047
        baseline.wall_time = _ignore_value_
        problem = zero_objective_max.ZeroObjectiveMax()
        self._execute_single_test(problem,
                                  baseline,
                                  relative_gap=0.0001,
                                  absolute_gap=0.0001)
        self._execute_single_test(problem,
                                  baseline,
                                  relative_gap=0.0,
                                  absolute_gap=0.0001)
        self._execute_single_test(problem,
                                  baseline,
                                  relative_gap=0.0001,
                                  absolute_gap=0.0)

    def test_zero_objective_min(self):
        baseline = SolverResults()
        baseline.solution_status = "optimal"
        baseline.termination_condition = "optimality"
        baseline.objective = 0.0
        baseline.bound = -0.0078125
        baseline.absolute_gap = 0.0078125
        baseline.relative_gap = 0.0078125
        baseline.nodes = 255
        baseline.wall_time = _ignore_value_
        problem = zero_objective_min.ZeroObjectiveMin()
        self._execute_single_test(problem,
                                  baseline,
                                  relative_gap=0.01,
                                  absolute_gap=0.01)
        self._execute_single_test(problem,
                                  baseline,
                                  relative_gap=0.0,
                                  absolute_gap=0.01)
        self._execute_single_test(problem,
                                  baseline,
                                  relative_gap=0.01,
                                  absolute_gap=0.0)

        baseline = SolverResults()
        baseline.solution_status = "optimal"
        baseline.termination_condition = "optimality"
        baseline.objective = 0.0
        baseline.bound = -0.0009765625
        baseline.absolute_gap = 0.0009765625
        baseline.relative_gap = 0.0009765625
        baseline.nodes = 2047
        baseline.wall_time = _ignore_value_
        problem = zero_objective_min.ZeroObjectiveMin()
        self._execute_single_test(problem,
                                  baseline,
                                  relative_gap=0.001,
                                  absolute_gap=0.001)
        self._execute_single_test(problem,
                                  baseline,
                                  relative_gap=0.0,
                                  absolute_gap=0.001)
        self._execute_single_test(problem,
                                  baseline,
                                  relative_gap=0.001,
                                  absolute_gap=0.0)

        baseline = SolverResults()
        baseline.solution_status = "feasible"
        baseline.termination_condition = "no_nodes"
        baseline.objective = 0.0
        baseline.bound = -0.0009765625
        baseline.absolute_gap = 0.0009765625
        baseline.relative_gap = 0.0009765625
        baseline.nodes = 2047
        baseline.wall_time = _ignore_value_
        problem = zero_objective_min.ZeroObjectiveMin()
        self._execute_single_test(problem,
                                  baseline,
                                  relative_gap=0.0001,
                                  absolute_gap=0.0001)
        self._execute_single_test(problem,
                                  baseline,
                                  relative_gap=0.0,
                                  absolute_gap=0.0001)
        self._execute_single_test(problem,
                                  baseline,
                                  relative_gap=0.0001,
                                  absolute_gap=0.0)
