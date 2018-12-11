"""
Branch-and-bound solver implementation.

Copyright by Gabriel A. Hackebeil (gabe.hackebeil@gmail.com).
"""
import array
import sys
import time
import math

from pybnb.common import nan
from pybnb.problem import (_SolveInfo,
                           _SimpleSolveInfoCollector,
                           _ProblemWithSolveInfoCollection)
from pybnb.misc import (metric_fmt,
                        as_stream,
                        get_simple_logger)
from pybnb.node import Node
from pybnb.convergence_checker import ConvergenceChecker
from pybnb.dispatcher_proxy import (DispatcherProxy,
                                    _termination_condition_to_int)
from pybnb.dispatcher import (DispatcherLocal,
                              DispatcherDistributed,
                              DispatcherQueueData)

try:
    import mpi4py
except ImportError:                               #pragma:nocover
    pass

import six

class _notset(object):
    pass

#
# used to transmit node_priority_strategy
#
_priority_to_int = {}
_priority_to_int["bound"] = 0
_priority_to_int["objective"] = 1
_priority_to_int["breadth"] = 2
_priority_to_int["depth"] = 3
_priority_to_int["fifo"] = 4
_priority_to_int["random"] = 5
_priority_to_int["custom"] = 6

_int_to_priority = [None]*7
_int_to_priority[0] = "bound"
_int_to_priority[1] = "objective"
_int_to_priority[2] = "breadth"
_int_to_priority[3] = "depth"
_int_to_priority[4] = "fifo"
_int_to_priority[5] = "random"
_int_to_priority[6] = "custom"

#
# used to transmit solution_status
#
_solution_status_to_int = {}
_solution_status_to_int["optimal"] = 0
_solution_status_to_int["feasible"] = 1
_solution_status_to_int["infeasible"] = 2
_solution_status_to_int["unbounded"] = 3
_solution_status_to_int["unknown"] = 4

_int_to_solution_status = [None]*5
_int_to_solution_status[0] = "optimal"
_int_to_solution_status[1] = "feasible"
_int_to_solution_status[2] = "infeasible"
_int_to_solution_status[3] = "unbounded"
_int_to_solution_status[4] = "unknown"

class SolverResults(object):
    """Stores the results of a branch-and-bound solve.

    Attributes
    ----------
    solution_status : {"optimal", "feasible", "infeasible", "unbounded", "unknown"}
        The solution status string.
    termination_condition : {"optimality", "feasibility", "cutoff", "node_limit", "time_limit", "node_nodes"}
        The solve termination condition string, as
        determined by the dispatcher.
    objective : float
        The best objective found.
    bound : float
        The global optimality bound.
    absolute_gap : float
        The absolute gap between the objective and bound.
    relative_gap : float
        The relative gap between the objective and bound.
    nodes : float
        The total number of nodes processes by all workers.
    wall_time : float
        The process-local wall time (seconds). This is the
        only value on the results object that varies between
        processes.
    """

    def __init__(self):
        self.solution_status = None
        self.termination_condition = None
        self.objective = None
        self.bound = None
        self.absolute_gap = None
        self.relative_gap = None
        self.nodes = None
        self.wall_time = None

    def pprint(self, stream=sys.stdout):
        """Prints a nicely formatted representation of the
        results.

        Parameters
        ----------
        stream : file-like object or string, optional
            A file-like object or a filename where results
            should be written to. (default: ``sys.stdout``)
        """
        with as_stream(stream) as stream:
            stream.write("solver results:\n")
            self.write(stream, prefix=" - ", pretty=True)

    def write(self, stream, prefix="", pretty=False):
        """Writes results in YAML format to a stream or
        file.

        Parameters
        ----------
        stream : file-like object or string
            A file-like object or a filename where results
            should be written to.
        prefix : string, optional
            A string to use as a prefix for each line that
            is written. (default: '')
        pretty : bool, optional
            Indicates whether or not certain recognized
            attributes should be formatted for more
            human-readable output. (default: False)
        """
        with as_stream(stream) as stream:
            attrs = vars(self)
            names = sorted(list(attrs.keys()))
            first = ('solution_status', 'termination_condition',
                     'objective', 'bound',
                     'absolute_gap', 'relative_gap',
                     'nodes', 'wall_time')
            for cnt, name in enumerate(first):
                if not hasattr(self, name):
                    continue
                names.remove(name)
                val = getattr(self, name)
                if val is not None:
                    if pretty:
                        if name == 'wall_time':
                            val = metric_fmt(val, unit='s')
                        elif name in ('objective','bound',
                                      'absolute_gap','relative_gap'):
                            val = "%.7g" % (val)
                stream.write(prefix+'%s: %s\n'
                             % (name, val))
            for name in names:
                stream.write(prefix+'%s: %s\n'
                              % (name, getattr(self, name)))

    def __str__(self):
        """Represents the results as a string."""
        tmp = six.StringIO()
        self.pprint(stream=tmp)
        return tmp.getvalue()

class Solver(object):
    """A branch-and-bound solver.

    Parameters
    ----------
    comm : ``mpi4py.MPI.Comm``, optional
        The MPI communicator to use. If unset, the
        mpi4py.MPI.COMM_WORLD communicator will be
        used. Setting this keyword to None will disable the
        use of MPI and avoid an attempted import of
        mpi4py.MPI (which avoids triggering a call to
        `MPI_Init()`).
    dispatcher_rank : int, optional
        The process with this rank will be designated as the
        dispatcher process. If MPI functionality is disabled
        (by setting comm=None), this keyword must be 0.
        (default: 0)
    """

    def __init__(self,
                 comm=_notset,
                 dispatcher_rank=0):
        mpi = True
        if comm is None:
            mpi = False
        self._comm = None
        self._worker_flag = None
        self._dispatcher_flag = None
        self._disp = None
        self._time = None
        if mpi:
            import mpi4py.MPI
            assert mpi4py.MPI.Is_initialized()
            assert comm is not None
            if comm is _notset:
                comm = mpi4py.MPI.COMM_WORLD
            if (int(dispatcher_rank) != dispatcher_rank) or \
               (dispatcher_rank < 0) or \
               (dispatcher_rank >= comm.size):
                raise ValueError("The 'dispatcher_rank' keyword "
                                 "has been set to %s, which is not "
                                 "an available rank given the "
                                 "size of the MPI communicator (%d)."
                                 % (dispatcher_rank, comm.size))
            self._comm = comm
            if comm.size > 1:
                dispatcher_rank = int(dispatcher_rank)
                if comm.rank == dispatcher_rank:
                    self._disp = DispatcherDistributed(comm)
                    self._worker_flag = False
                    self._dispatcher_flag = True
                else:
                    self._disp = DispatcherProxy(comm)
                    self._worker_flag = True
                    self._dispatcher_flag = False
            else:
                self._disp = DispatcherLocal()
                self._worker_flag = True
                self._dispatcher_flag = True
            self._time = mpi4py.MPI.Wtime
        else:
            if dispatcher_rank != 0:
                raise ValueError(
                    "MPI functionality has been disabled but "
                    "the 'dispatcher_rank' keyword is set to "
                    "something other than 0.")
            assert self._comm is None
            self._disp = DispatcherLocal()
            self._worker_flag = True
            self._dispatcher_flag = True
            self._time = time.time
        assert self._worker_flag in (True, False)
        assert self._dispatcher_flag in (True, False)
        assert self._disp is not None
        assert self._time is not None
        self._wall_time = None
        self._best_objective = None
        self._local_solve_info = None
        self._global_solve_info = None

    def _reset_local_solve_stats(self):
        self._wall_time = 0.0
        self._best_objective = None
        self._local_solve_info = _SolveInfo()
        self._global_solve_info = None

    def _check_update_best_objective(self,
                                     converger,
                                     new_objective):
        if converger.objective_improved(new_objective,
                                        self._best_objective):
            self._best_objective = new_objective
            return True
        else:
            return False

    def _fill_results(self, results, converger):
        if results.bound == converger.infeasible_objective:
            assert results.objective == converger.infeasible_objective, \
                str(results.objective)
            results.solution_status = "infeasible"
        elif results.objective == converger.infeasible_objective:
            results.solution_status = "unknown"
        elif results.objective == converger.unbounded_objective:
            assert results.bound == converger.unbounded_objective, \
                str(results.bound)
            results.solution_status = "unbounded"
        else:
            results.absolute_gap = converger.\
                                   compute_absolute_gap(results.bound,
                                                        results.objective)
            results.relative_gap = converger.\
                                   compute_relative_gap(results.bound,
                                                        results.objective)
            if converger.objective_is_optimal(results.objective,
                                              results.bound):
                results.solution_status = "optimal"
            else:
                results.solution_status = "feasible"

    def _solve(self,
               problem,
               best_objective,
               converger,
               results):
        infeasible_objective = problem.infeasible_objective()
        assert infeasible_objective == converger.infeasible_objective
        unbounded_objective = problem.unbounded_objective()
        assert unbounded_objective == converger.unbounded_objective

        self._best_objective = best_objective
        children = ()
        bound = unbounded_objective
        if not isinstance(problem, _ProblemWithSolveInfoCollection):
            problem = _SimpleSolveInfoCollector(problem)
            problem.set_clock(self._time)
        problem.set_solve_info_object(self._local_solve_info)

        working_node = Node()
        assert working_node.tree_id is None

        # start the work loop
        while (1):
            update_start = self._time()
            stop, new_objective, data = \
                self._disp.update(
                    self._best_objective,
                    bound,
                    self._local_solve_info,
                    children)
            update_stop = self._time()
            self._local_solve_info.total_queue_time += \
                update_stop-update_start
            self._local_solve_info.queue_call_count += 1

            updated = self._check_update_best_objective(
                converger,
                new_objective)
            if updated:
                problem.notify_new_best_objective_received(
                    self.worker_comm,
                    self._best_objective)
            del updated

            children = []

            if stop:
                # make sure all processes have the exact same best
                # objective value (not just subject to tolerances)
                self._best_objective = new_objective
                break
            # load the new data into the working_node
            working_node._set_data(data)
            del new_objective
            del data

            bound = working_node.bound
            current_tree_id = working_node.tree_id
            current_tree_depth = working_node.tree_depth
            assert current_tree_id is not None
            assert current_tree_depth >= 0

            # we should not be receiving a node that
            # does not satisfy these assertions
            assert (bound != infeasible_objective) and \
                converger.objective_can_improve(
                    self._best_objective,
                    bound) and \
                    (not converger.cutoff_is_met(bound))

            problem.load_state(working_node)

            new_bound = problem.bound()
            if converger.bound_worsened(new_bound, bound):    #pragma:nocover
                self._disp.log_warning(
                    "WARNING: Bound became worse "
                    "(old=%r, new=%r)"
                    % (bound, new_bound))
            working_node.bound = new_bound
            bound = new_bound

            if (bound != infeasible_objective) and \
                converger.objective_can_improve(
                    self._best_objective,
                    bound) and \
                (not converger.cutoff_is_met(bound)):
                obj = problem.objective()
                working_node.objective = obj
                if obj is not None:
                    if converger.bound_is_suboptimal(bound, obj): #pragma:nocover
                        self._disp.log_warning(
                            "WARNING: Local node bound is worse "
                            "than local node objective (bound=%r, "
                            "objective=%r)" % (bound, obj))
                    updated = self._check_update_best_objective(
                        converger,
                        obj)
                    if updated:
                        problem.notify_new_best_objective(
                            self.worker_comm,
                            self._best_objective)
                    del updated
                if (obj != converger.unbounded_objective) and \
                   converger.objective_can_improve(
                       self._best_objective,
                       bound):
                    clist = problem.branch(working_node)
                    for child in clist:
                        assert child.parent_tree_id == current_tree_id
                        assert child.tree_id is None
                        assert child.tree_depth >= current_tree_depth + 1
                        children.append(child._data)
                        if converger.bound_worsened(child.bound, bound):    #pragma:nocover
                            self._disp.log_warning(
                                "WARNING: Bound on child node "
                                "returned from branch method "
                                "is worse than parent node "
                                "(child=%r, parent=%r)"
                                % (bound, child.bound))

        assert len(data) == 3
        global_bound = data[0]
        termination_condition = data[1]
        global_solve_info = data[2]
        return (self._best_objective,
                global_bound,
                termination_condition,
                global_solve_info)

    #
    # Interface
    #

    @property
    def is_worker(self):
        """Indicates if this process has been designated as
        a worker."""
        return self._worker_flag

    @property
    def is_dispatcher(self):
        """Indicates if this process has been designated as
        the dispatcher."""
        return self._dispatcher_flag

    @property
    def comm(self):
        """The full MPI communicator that includes the
        dispatcher and all workers. Will be None if MPI
        functionality has been disabled."""
        return self._comm

    @property
    def worker_comm(self):
        """The worker MPI communicator. Will be None on any
        processes for which :attr:`Solver.is_worker` is
        False, or if MPI functionality has been disabled."""
        if (self._comm is None) or \
           (self._comm.size == 1):
            return self._comm
        elif not self.is_dispatcher:
            return self._disp.worker_comm
        return None

    @property
    def worker_count(self):
        """The number of worker processes associated with this solver."""
        if (self._comm is None) or \
           (self._comm.size == 1):
            return 1
        elif not self.is_dispatcher:
            return self._disp.worker_comm.size
        else:
            return len(self._disp.worker_ranks)

    def collect_worker_statistics(self):
        """Collect individual worker statistics about the
        most recent solve.

        Returns
        -------
        dict
            A dictionary whose keys are the different
            statistics collected, where each entry is a list
            storing a value for each worker.
        """
        import numpy
        stats = {}
        if (self.comm is not None) and \
           (self.comm.size > 1):
            gathered = numpy.empty((self.worker_count, 10),
                                   dtype=float)
            if self.is_worker:
                assert self.worker_comm is not None
                assert not self.is_dispatcher
                solve_info = self._local_solve_info
                mine = numpy.array(
                    [self._wall_time,
                     solve_info.total_queue_time,
                     solve_info.queue_call_count,
                     solve_info.total_objective_time,
                     solve_info.objective_call_count,
                     solve_info.total_bound_time,
                     solve_info.bound_call_count,
                     solve_info.total_branch_time,
                     solve_info.branch_call_count,
                     solve_info.explored_nodes_count],
                    dtype=float)
                assert len(mine) == gathered.shape[1]
                self.worker_comm.Allgather([mine, mpi4py.MPI.DOUBLE],
                                           [gathered, mpi4py.MPI.DOUBLE])
                if self.worker_comm.rank == 0:
                    self.comm.Send([gathered, mpi4py.MPI.DOUBLE],
                                   self._disp.dispatcher_rank,
                                   tag=11112111)
            else:
                assert self.worker_comm is None
                assert self.is_dispatcher
                self.comm.Recv([gathered, mpi4py.MPI.DOUBLE],
                               source=self._disp.root_worker_rank,
                               tag=11112111)
            gathered = gathered.T.tolist()
            stats['wall_time'] = gathered[0]
            stats['queue_time'] = gathered[1]
            stats['queue_call_count'] = gathered[2]
            stats['objective_time'] = gathered[3]
            stats['objective_call_count'] = gathered[4]
            stats['bound_time'] = gathered[5]
            stats['bound_call_count'] = gathered[6]
            stats['branch_time'] = gathered[7]
            stats['branch_call_count'] = gathered[8]
            stats['explored_nodes_count'] = gathered[9]
        else:
            assert self.is_worker
            assert self.is_dispatcher
            solve_info = self._local_solve_info
            stats['wall_time'] = [self._wall_time]
            stats['queue_time'] = [solve_info.total_queue_time]
            stats['queue_call_count'] = [solve_info.queue_call_count]
            stats['objective_time'] = \
                [solve_info.total_objective_time]
            stats['objective_call_count'] = \
                [solve_info.objective_call_count]
            stats['bound_time'] = \
                [solve_info.total_bound_time]
            stats['bound_call_count'] = \
                [solve_info.bound_call_count]
            stats['branch_time'] = \
                [solve_info.total_branch_time]
            stats['branch_call_count'] = \
                [solve_info.branch_call_count]
            stats['explored_nodes_count'] = \
                [solve_info.explored_nodes_count]

        return stats

    def save_dispatcher_queue(self):
        """Saves the dispatcher queue.

        Returns
        -------
        queue : :class:`pybnb.dispatcher.DispatcherQueueData` or None
            If this process is the dispatcher, this method
            will return an object storing any nodes
            currently in the dispatcher queue.  If this
            process is not the dispatcher, this method will
            return None.  The returned object can be used to
            reinitialize a solve (e.g., with different
            algorithms settings) by assigning it to the
            initialize_queue keyword of the
            :func:`Solver.solve` method.
        """
        ret = None
        if self.is_dispatcher:
            ret = self._disp.save_dispatcher_queue()
        return ret

    def solve(self,
              problem,
              best_objective=None,
              initialize_queue=None,
              node_priority_strategy="bound",
              absolute_gap=1e-8,
              relative_gap=1e-4,
              cutoff=None,
              node_limit=None,
              time_limit=None,
              absolute_tolerance=1e-10,
              log_interval_seconds=1.0,
              log=_notset):
        """Solve a problem using branch-and-bound.

        Parameters
        ----------
        problem : :class:`pybnb.Problem <pybnb.problem.Problem>`
            An object defining a branch-and-bound problem.
        best_objective : float, optional
            Initializes the solve with an assumed best
            objective. (default: None)
        initialize_queue : :class:`pybnb.dispatcher.DispatcherQueueData`, optional
            Initializes the dispatcher queue with that
            remaining from a previous solve (obtained by
            calling :func:`Solver.save_dispatcher_queue`
            after the solve). If left as None, the queue
            will be initialized with a single root node
            created by calling :func:`problem.save_state
            <pybnb.problem.Problem.save_state`.
            (default: None)
        node_priority_strategy : {"bound", "objective", "breadth", "depth", "fifo", "random", "custom"}, optional
            Indicates the strategy for ordering nodes in the
            work queue. The "bound" strategy always selects
            the node with the worst bound first. The
            "objective" strategy always selects the node
            with the best objective first. The "breadth"
            strategy always selects the node with the
            smallest tree depth first (i.e., breadth-first
            search). The "depth" strategy always selects the
            node with the largest tree depth first (i.e.,
            depth-first search). The "fifo" strategy selects
            nodes in first-in, first-out order. The "random"
            strategy assigns a random priority to each
            node. The "custom" strategy assumes the
            :attr:`queue_priority <pybnb.node.Node.queue_priority>`
            node attribute has been set by the user. For all
            other strategies, the :attr:`queue_priority
            <pybnb.node.Node.queue_priority>` node attribute
            will be set automatically (any existing value
            will be overwritten). In all cases, the node
            with the largest priority in the queue is always
            selected next, with ties being broken by
            insertion order. (default: "bound")
        absolute_gap : float, optional
            The solver will terminate with an optimal status
            when the absolute gap between the objective and
            bound is less than this value. (default: 1e-8)
        relative_gap : float, optional
            The solver will terminate with an optimal status
            when the relative gap between the objective and
            bound is less than this value. (default: 1e-4)
        cutoff : float, optional
            If provided, when the best objective is proven
            worse than this value, the solver will begin to
            terminate, and the termination_condition flag on
            the results object will be set to the string
            "cutoff". (default: None)
        node_limit : int, optional
            If provided, the solver will begin to terminate
            once this many nodes have been processed. It is
            possible that more nodes will be processed when
            running there are multiple workers, but not by
            more than the number of available workers. If
            this setting initiates a shutdown, then the
            termination_condition flag on the results object
            will be set to the string
            "node_limit". (default: None)
        time_limit : float, optional
            If provided, the solver will begin to terminate
            the solve once this amount of time has
            passed. The solver may run for an arbitrarily
            longer amount of time, depending how long
            workers spend processing their current node. If
            this setting initiates a shutdown, then the
            termination_condition flag on the results object
            will be set to the string
            "time_limit". (default: None)
        absolute_tolerance : float, optional
            The absolute tolerance used when deciding if two
            objective / bound values are sufficiently
            different. For instance, this option controls
            what nodes are added to the queue by checking if
            their bound is at this much better than the
            current best object. (default: 1e-10)
        log_interval_seconds : float, optional
            The approximate maximum time (in seconds)
            between solver log updates. More time may pass
            between log updates if no updates have been
            received from any workers, and less time may
            pass if a new incumbent is found. (default: 1.0)
        log : logging.Logger, optional
            A log object where solver output should be
            sent. The default value causes all output to be
            streamed to the console. Setting to None
            disables all output.

        Returns
        -------
        results : :class:`SolverResults`
            An object storing information about the solve.
        """
        self._reset_local_solve_stats()
        start = self._time()

        if best_objective is None:
            best_objective = problem.infeasible_objective()

        if node_priority_strategy not in _priority_to_int:
            raise ValueError("The 'node_priority_strategy' keyword "
                             "must be one of: %s"
                             % (str(sorted(_priority_to_int.keys()))))

        # broadcast options from dispatcher to everyone else
        # to ensure consistency
        if (self.comm is not None) and \
           (self.comm.size > 1):
            node_priority_strategy_int = \
                _priority_to_int[node_priority_strategy]
            settings = array.array("d", [best_objective,
                                         node_priority_strategy_int,
                                         absolute_gap,
                                         relative_gap,
                                         cutoff if cutoff is not None else nan,
                                         absolute_tolerance])
            self.comm.Bcast([settings,mpi4py.MPI.DOUBLE],
                            root=self._disp.dispatcher_rank)
            (best_objective,
             node_priority_strategy_int,
             absolute_gap,
             relative_gap,
             cutoff,
             absolute_tolerance) = settings
            assert node_priority_strategy_int == \
                int(node_priority_strategy_int)
            node_priority_strategy = \
                _int_to_priority[int(node_priority_strategy_int)]
            if math.isnan(cutoff):
                cutoff = None
            del settings
            if not self.is_dispatcher:
                # These are not used unless this process is
                # the dispatcher
                node_limit = None
                time_limit = None
                log_interval_seconds = None
                log = None
                if initialize_queue is not None:       #pragma:nocover
                    raise ValueError("The 'initialize_queue' keyword "
                                     "must be None for all processes "
                                     "except the dispatcher.")

        results = SolverResults()
        converger = ConvergenceChecker(
            problem.sense(),
            absolute_gap=absolute_gap,
            relative_gap=relative_gap,
            absolute_tolerance=absolute_tolerance,
            cutoff=cutoff)
        root = Node()
        problem.save_state(root)
        try:
            if self.is_dispatcher:
                if initialize_queue is None:
                    root.bound = problem.unbounded_objective()
                    root.objective = best_objective
                    assert root.tree_id is None
                    Node._insert_tree_id(root._data, 0)
                    initialize_queue = DispatcherQueueData(
                        nodes=[Node(data_=root._data.copy())],
                        next_tree_id=1)
                if log is _notset:
                    log = get_simple_logger()
                self._disp.initialize(
                    best_objective,
                    initialize_queue,
                    node_priority_strategy,
                    converger,
                    node_limit,
                    time_limit,
                    log,
                    log_interval_seconds)
            if not self.is_worker:
                tmp = self._disp.serve()
            else:
                tmp = self._solve(problem,
                                  best_objective,
                                  converger,
                                  results)
            (results.objective,
             results.bound,
             results.termination_condition,
             self._global_solve_info) = tmp
            results.nodes = self._global_solve_info.explored_nodes_count
            self._fill_results(results, converger)
        except:                                        #pragma:nocover
            sys.stderr.write("Exception caught: "+str(sys.exc_info()[1])+"\n")
            sys.stderr.write("Attempting to shut down, but this may hang.\n")
            sys.stderr.flush()
            raise
        finally:
            problem.load_state(root)
        stop = self._time()
        self._wall_time = stop-start
        results.wall_time = self._wall_time

        assert results.solution_status in \
            _solution_status_to_int, str(results)
        assert results.termination_condition in \
            _termination_condition_to_int, str(results)

        problem.notify_solve_finished(self.comm,
                                      self.worker_comm,
                                      results)
        if self.is_dispatcher and \
           (log is not None) and \
           (not log.disabled):
            self._disp.log_info("")
            if results.solution_status in ("feasible", "optimal"):
                agap = converger.compute_absolute_gap(
                    results.bound,
                    results.objective)
                rgap = converger.compute_relative_gap(
                    results.bound,
                    results.objective)
                if results.solution_status == "feasible":
                    self._disp.log_info("Feasible solution found")
                else:
                    if agap < converger.absolute_gap_tolerance:
                        self._disp.log_info("Absolute optimality tolerance met")
                    if rgap < converger.relative_gap_tolerance:
                        self._disp.log_info("Relative optimality tolerance met")
                    assert results.solution_status == "optimal"
                    self._disp.log_info("Optimal solution found")
                self._disp.log_info(" - absolute gap: %.6g"
                                    % (agap))
                self._disp.log_info(" - relative gap: %.6g"
                                    % (rgap))
            elif results.solution_status == "infeasible":
                self._disp.log_info("Problem is infeasible")
            elif results.solution_status == "unbounded":
                self._disp.log_info("Problem is unbounded")
            else:
                assert results.solution_status == "unknown"
                self._disp.log_info("Status unknown")
            self._disp.log_info("")
            self._disp.log_info(str(results))

        return results

def summarize_worker_statistics(stats, stream=sys.stdout):
    """Writes a summary of workers statistics to an
    output stream.

    Parameters
    ----------
    stats : dict
        A dictionary of worker statistics returned from
        a call to :func:`collect_worker_statistics`.
    stream : file-like object, or string, optional
        A file-like object or a filename where results
        should be written to. (default: ``sys.stdout``)
    """
    import numpy
    explored_nodes_count = numpy.array(stats['explored_nodes_count'],
                                       dtype=int)
    wall_time = numpy.array(stats['wall_time'],
                            dtype=float)
    queue_time = numpy.array(stats['queue_time'],
                              dtype=float)
    queue_call_count = numpy.array(stats['queue_call_count'],
                               dtype=int)
    objective_time = numpy.array(stats['objective_time'],
                                 dtype=float)
    objective_call_count = numpy.array(stats['objective_call_count'],
                                  dtype=int)
    bound_time = numpy.array(stats['bound_time'],
                             dtype=float)
    bound_call_count = numpy.array(stats['bound_call_count'],
                              dtype=int)
    branch_time = numpy.array(stats['branch_time'],
                              dtype=float)
    branch_call_count = numpy.array(stats['branch_call_count'],
                               dtype=int)
    work_time = wall_time - queue_time

    with as_stream(stream) as stream:
        stream.write("Number of Workers:   %6d\n"
                     % (len(wall_time)))
        div = max(1.0,numpy.mean(explored_nodes_count))
        numerator = numpy.max(explored_nodes_count) - \
            numpy.min(explored_nodes_count)
        if explored_nodes_count.sum() == 0:
            stream.write("Load Imbalance:     %6.2f%%\n"
                         % (0.0))
        else:
            stream.write("Load Imbalance:     %6.2f%%\n"
                         % (numerator/div*100.0))
        stream.write("Average Worker Timing:\n")
        queue_call_count_str = "%d" % queue_call_count.sum()
        tmp = "%"+str(len(queue_call_count_str))+"d"
        bound_call_count_str = tmp % bound_call_count.sum()
        objective_call_count_str = tmp % objective_call_count.sum()
        branch_call_count_str = tmp % branch_call_count.sum()
        div1 = numpy.copy(wall_time)
        div1[div1 == 0] = 1
        div2 = numpy.copy(queue_call_count)
        div2[div2 == 0] = 1
        stream.write(" - queue:     %6.2f%% [avg time: %8s, count: %s]\n"
                     % (numpy.mean(queue_time/div1)*100.0,
                        metric_fmt(numpy.mean(queue_time/div2),
                                   unit='s',
                                   align_unit=True),
                        queue_call_count_str))
        div2 = numpy.copy(bound_call_count)
        div2[div2==0] = 1
        stream.write(" - bound:     %6.2f%% [avg time: %8s, count: %s]\n"
                     % (numpy.mean((bound_time/div1))*100.0,
                        metric_fmt(numpy.mean(bound_time/div2),
                                   unit='s',
                                   align_unit=True),
                        bound_call_count_str))
        div2 = numpy.copy(objective_call_count)
        div2[div2==0] = 1
        stream.write(" - objective: %6.2f%% [avg time: %8s, count: %s]\n"
                     % (numpy.mean((objective_time/div1))*100.0,
                        metric_fmt(numpy.mean(objective_time/div2),
                                   unit='s',
                                   align_unit=True),
                        objective_call_count_str))
        div2 = numpy.copy(branch_call_count)
        div2[div2==0] = 1
        stream.write(" - branch:    %6.2f%% [avg time: %8s, count: %s]\n"
                     % (numpy.mean((branch_time/div1))*100.0,
                        metric_fmt(numpy.mean(branch_time/div2),
                                   unit='s',
                                   align_unit=True),
                        branch_call_count_str))
        other_time = work_time - objective_time - bound_time - branch_time
        div2 = numpy.copy(queue_call_count)
        div2[div2 == 0] = 1
        stream.write(" - other:     %6.2f%% [avg time: %8s, count: %s]\n"
                     % (numpy.mean(other_time/div1)*100.0,
                        metric_fmt(numpy.mean(other_time/div2),
                                   unit='s',
                                   align_unit=True),
                        queue_call_count_str))


def solve(problem,
          comm=_notset,
          dispatcher_rank=0,
          log_filename=None,
          results_filename=None,
          **kwds):
    """Solves a branch-and-bound problem and returns the
    solution.

    Note
    ----
    This function also collects and summarizes runtime
    workload statistics, which may introduce additional
    overhead. This overhead can be avoided by directly
    instantiating a :class:`Solver` object and
    calling the :func:`Solver.solve` method.

    Parameters
    ----------
    problem : :class:`pybnb.Problem <pybnb.problem.Problem>`
        An object that defines a branch-and-bound problem
    comm : ``mpi4py.MPI.Comm``, optional
        The MPI communicator to use. If unset, the
        mpi4py.MPI.COMM_WORLD communicator will be
        used. Setting this keyword to None will disable the
        use of MPI and avoid an attempted import of
        mpi4py.MPI (which avoids triggering a call to
        `MPI_Init()`).
    dispatcher_rank : int, optional
        The process with this rank will be designated the
        dispatcher process. If MPI functionality is disabled
        (by setting comm=None, or when comm.size==1), this
        keyword must be left at 0. (default: 0)
    log_filename : string, optional
        A filename where solver output should be sent in
        addition to console. This keyword will be ignored if
        the `log` keyword is set. (default: None)
    results_filename : string, optional
        Saves the solver results into a YAML-formatted file
        with the given name. (default: None)
    **kwds
        Additional keywords to be passed to
        :func:`Solver.solve`. See that method for additional
        keyword documentation.

    Returns
    -------
    results : :class:`SolverResults`
        An object storing information about the solve.
    """

    opt = Solver(comm=comm)

    if (opt.is_dispatcher) and \
       ("log" not in kwds) and \
       (log_filename is not None):
        kwds["log"] = get_simple_logger(
            filename=log_filename)

    results = opt.solve(problem, **kwds)

    stats = opt.collect_worker_statistics()
    if opt.is_dispatcher:
        tmp = six.StringIO()
        summarize_worker_statistics(stats, stream=tmp)
        opt._disp.log_info(tmp.getvalue())

    if opt.is_dispatcher and (results_filename is not None):
        results.write(results_filename)

    return results
