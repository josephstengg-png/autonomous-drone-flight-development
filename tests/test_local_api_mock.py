"""The mock local API (docs/local-app-plan.md) enforces the console's rules, so scripts written against it meet the same
refusals the real API will give."""
import pytest

from artex.console.local_api_mock import ApiError, MockConsole, START_WINDOW_S


def _ready(c=None, **kw):
    c = c or MockConsole(**kw)
    c.first_operator("Ops Lead", "482916")
    c.sign_in("Ops Lead", "482916")
    c.set_crew({113: "Pilot A", 115: "Pilot B"})
    c.preflight(113); c.preflight(115)
    p = dict(name="pair", mode="mission", aircraft=[dict(sysid=113, plan="a"), dict(sysid=115, plan="b")])
    c.accept_risks("fleet", p)
    tok = c.authorise("fleet", p, confirm="AUTHORISE")["token"]
    c.start("fleet", p, tok)
    while c.status()["state"] != "ready":
        pass
    return c, p, tok


def test_nothing_works_before_sign_in():
    with pytest.raises(ApiError) as e:
        MockConsole().preflight(113)
    assert e.value.status == 401


def test_authorise_needs_pilots_risks_and_the_confirmation_word():
    c = MockConsole()
    c.first_operator("Ops Lead", "482916"); c.sign_in("Ops Lead", "482916")
    p = dict(name="pair", aircraft=[dict(sysid=113), dict(sysid=115)])
    with pytest.raises(ApiError) as e:
        c.authorise("fleet", p, confirm="AUTHORISE")
    assert e.value.status == 403 and "Preflight" in e.value.message
    c.preflight(113); c.preflight(115)
    for setup, word, code in ((lambda: None, "AUTHORISE", 409), (lambda: c.accept_risks("fleet", p), "AUTHORISE", 409),
                              (lambda: c.set_crew({113: "A", 115: "B"}), "yes", 403)):
        setup()
        with pytest.raises(ApiError) as e:
            c.authorise("fleet", p, confirm=word)
        assert e.value.status == code
    assert c.authorise("fleet", p, confirm="AUTHORISE")["token"]


def test_a_changed_profile_voids_the_token():
    c, p, tok = _ready()
    c.cancel()
    with pytest.raises(ApiError, match="changed"):
        c.start("fleet", dict(p, name="edited"), tok)


def test_gates_need_their_state_and_their_word():
    c, p, tok = _ready()
    with pytest.raises(ApiError) as e:
        c.start_all(tok, confirm="START ALL")           # gate 2 before gate 1
    assert e.value.status == 409
    with pytest.raises(ApiError):
        c.arm_all(tok, confirm="ARM")                   # wrong word
    c.arm_all(tok, confirm="ARM ALL")
    assert all(a["armed"] and a["mode"].endswith("(HOLD)") for a in c.status()["aircraft"])   # armed in Hold


def test_a_late_start_disarms_everyone():
    c, p, tok = _ready()
    c.arm_all(tok, confirm="ARM ALL")
    c.now += START_WINDOW_S + 1
    with pytest.raises(ApiError, match="Too late"):
        c.start_all(tok, confirm="START ALL")
    assert not any(a["armed"] for a in c.status()["aircraft"])


def test_an_observer_may_stop_but_not_arm():
    c, p, tok = _ready()
    c.sign_out(); c.sign_in("Ops Lead", "482916"); c.add_operator("Watcher", "1111", "observer")
    c.sign_out(); c.sign_in("Watcher", "1111")
    with pytest.raises(ApiError) as e:
        c.arm_all(tok, confirm="ARM ALL")
    assert e.value.status == 403
    assert c.stop("HOLD") == dict(sent=[113, 115])


def test_a_fault_holds_the_others_and_the_run_is_not_called_complete():
    c, p, tok = _ready(fault_on=113)
    c.arm_all(tok, confirm="ARM ALL"); c.start_all(tok, confirm="START ALL")
    for _ in range(20):
        s = c.status()
        if s["state"] == "fault":
            break
    assert s["state"] == "fault" and next(a for a in s["aircraft"] if a["sysid"] == 115)["mode"].endswith("(HOLD)")
    c.stop("RTL", 115)
    while not (s := c.status())["result"]:
        pass
    assert s["result"]["passed"] is False
