from roadie.engine.inputs import run_scope, run_state


# (spec) Input fingerprints
def test_run_state_is_shared_within_a_run_and_released_after_it() -> None:
    with run_scope():
        first = run_state("key", list)
        first.append(1)
        assert run_state("key", list) is first
        assert run_state("other", list) is not first

    assert run_state("key", list) == []
    with run_scope():
        assert run_state("key", list) == []


# (spec) Input fingerprints
def test_run_state_outside_a_run_is_new_on_every_call() -> None:
    assert run_state("key", list) is not run_state("key", list)
