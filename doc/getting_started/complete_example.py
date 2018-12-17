# complete_example.py

import pybnb

class Simple(pybnb.Problem):
    def __init__(self):
        self._xL, self._xU = 0, 1
    #
    # required methods
    #
    def sense(self):
        return pybnb.minimize
    def objective(self):
        return round(self._xU-self._xL,3)
    def bound(self):
        return -(self._xU - self._xL)**2
    def save_state(self, node):
        node.resize(2)
        node.state[0] = self._xL
        node.state[1] = self._xU
    def load_state(self, node):
        self._xL = float(node.state[0])
        self._xU = float(node.state[1])
    def branch(self, parent_node):
        xL, xU = self._xL, self._xU
        xM = 0.5 * (xL + xU)
        self._xL, self._xU = xL, xM
        left = parent_node.new_child()
        self.save_state(left)
        self._xL, self._xU = xM, xU
        right = parent_node.new_child()
        self.save_state(right)
        self._xL, self._xU = xL, xU
        return left, right
    #
    # optional methods
    #
    def notify_new_best_objective_received(self,
                                           worker_comm,
                                           best_objective):
        pass
    def notify_new_best_objective(self,
                                  worker_comm,
                                  best_objective):
        pass
    def notify_solve_finished(self,
                              comm,
                              worker_comm,
                              results):
        pass

problem = Simple()
solver = pybnb.Solver()
results = solver.solve(problem)
