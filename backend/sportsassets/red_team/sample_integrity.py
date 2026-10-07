from __future__ import annotations

def independent_sample_gate(*, decision_rows:int, independent_events:int,
                            train_events:set[str], test_events:set[str],
                            holdout_events:set[str], holdout_open_count:int,
                            min_independent_events:int=100)->dict:
    blockers=[]
    if independent_events<min_independent_events:
        blockers.append("INSUFFICIENT_INDEPENDENT_EVENTS")
    if train_events & test_events: blockers.append("TRAIN_TEST_EVENT_LEAKAGE")
    if train_events & holdout_events: blockers.append("TRAIN_HOLDOUT_EVENT_LEAKAGE")
    if test_events & holdout_events: blockers.append("TEST_HOLDOUT_EVENT_LEAKAGE")
    if holdout_open_count>1: blockers.append("HOLDOUT_OPENED_MORE_THAN_ONCE")
    inflation=None if independent_events<=0 else decision_rows/independent_events
    return {"green":not blockers,"blockers":tuple(blockers),
            "decision_rows":decision_rows,"independent_events":independent_events,
            "row_to_event_ratio":inflation}

def multiple_testing_gate(*, candidates_tested:int, pre_registered:int,
                          selected_after_holdout:bool,
                          pbo_ok:bool, dsr_ok:bool)->dict:
    blockers=[]
    if candidates_tested>pre_registered: blockers.append("UNREGISTERED_CANDIDATES_TESTED")
    if selected_after_holdout: blockers.append("HOLDOUT_USED_FOR_SELECTION")
    if not pbo_ok: blockers.append("PBO_NOT_ACCEPTABLE")
    if not dsr_ok: blockers.append("DSR_NOT_ACCEPTABLE")
    return {"green":not blockers,"blockers":tuple(blockers)}
