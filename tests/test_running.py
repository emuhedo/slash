# pylint: disable-msg=W0201
from .utils.test_generator import TestGenerator
from shakedown.runner import run_tests
from shakedown.session import Session
from shakedown.suite import Suite
from shakedown.result import Result
from shakedown.ctx import context
import six # pylint: disable=F0401
import random
from .utils import TestCase

class TestRunningTestBase(TestCase):
    def setUp(self):
        super(TestRunningTestBase, self).setUp()
        self.generator = TestGenerator()
        self.total_num_tests = 10
        self.runnables = [t.generate_test() for t in self.generator.generate_tests(self.total_num_tests)]
        with Session() as session:
            self.session = session
            with Suite() as suite:
                context.current_test_generator = self.generator
                self.suite = suite
                self.prepare_runnables()
                run_tests(self.runnables)
    def prepare_runnables(self):
        pass

class AllSuccessfulTest(TestRunningTestBase):
    def test__all_executed(self):
        self.generator.assert_all_run()

_RESULT_PREDICATES = set([
    getattr(Result, method_name)
    for method_name in dir(Result) if method_name.startswith("is_")
    ])

class FailedItemsTest(TestRunningTestBase):
    def prepare_runnables(self):
        num_unsuccessfull = len(self.runnables) // 2
        num_error_tests = 2
        assert 1 < num_unsuccessfull < len(self.runnables)
        unsuccessful = random.sample(self.runnables, num_unsuccessfull)
        self.error_tests = [unsuccessful.pop(-1) for _ in six.moves.xrange(num_error_tests)]
        self.skipped_tests = [unsuccessful.pop(-1)]
        self.failed_tests = unsuccessful
        assert self.error_tests and self.failed_tests
        for failed_test in self.failed_tests:
            self.generator.make_test_fail(failed_test)
        for skipped_test in self.skipped_tests:
            self.generator.make_test_skip(skipped_test)
        for error_test in self.error_tests:
            self.generator.make_test_raise_exception(error_test)
    def test__all_executed(self):
        self.generator.assert_all_run()
    def test__failed_items_failed(self):
        self._test_results(self.failed_tests, [Result.is_finished, Result.is_failure, Result.is_just_failure])
    def test__error_items_error(self):
        self._test_results(self.error_tests, [Result.is_finished, Result.is_error])
    def test__skipped_items_skipped(self):
        self._test_results(self.skipped_tests, [Result.is_finished, Result.is_skip])
    def _test_results(self, tests, true_predicates):
        true_predicates = set(true_predicates)
        assert _RESULT_PREDICATES >= true_predicates, "{0} is not a superset of {1}".format(_RESULT_PREDICATES, true_predicates)
        for test in tests:
            result = self.suite.get_result(test)
            for predicate in _RESULT_PREDICATES:
                predicate_result = predicate(result)
                self.assertEquals(predicate_result,
                                  predicate in true_predicates,
                                  "Predicate {0} unexpectedly returned {1}".format(predicate, predicate_result))

### make nosetests ignore stuff we don't want to run
run_tests.__test__ = False