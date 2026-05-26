"""0514 SOP coverage.

- rule-level present/missing paths
- Excel SOP item to rule_id mapping
- realistic clean flow
- alert then remedy flow
- skip-stage behavior
- wording variants
- negative cases and expected gap tracking
"""
import importlib
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from match.engine import tick
from match.rules_util import iter_must_rules
from match.session import Session
from sop_rules import SOP_RULES


def _feed(session, *texts):
    events = []
    for text in texts:
        events.extend(tick(text, session, SOP_RULES))
    return events


def _feed_no_alert(session, *texts):
    events = _feed(session, *texts)
    unexpected = [event for event in events if event.type in ("must_timeout", "forbidden")]
    assert not unexpected, [(event.type, event.rule_id, event.message) for event in unexpected]
    return events


def _start_product_pitch(session):
    session.product_pitch_since = time.time() - 3
    _feed_no_alert(session, "继续看一下整体表现")


def _start_retry_invite(session):
    _feed_no_alert(session, "你好欢迎", "我是销售顾问", "给您介绍这款车", "先回去考虑")
    _age_rule(session, "invite_test_drive")
    tick(None, session, SOP_RULES)
    _feed_no_alert(session, "我再帮您争取一下")


def _age_rule(session, rule_id):
    state = session.rule(rule_id)
    state.armed_at = time.time() - 999


def _silence_other_rules(session, rule_id):
    for rid, state in session.rule_states.items():
        if rid != rule_id and state.status == "armed":
            state.status = "done"


def _assert_armed(session, rule_id):
    assert session.rule(rule_id).status == "armed", (
        rule_id,
        {rid: st.status for rid, st in session.rule_states.items()},
    )


def _assert_done(session, rule_id):
    assert session.rule(rule_id).status == "done", (
        rule_id,
        {rid: st.status for rid, st in session.rule_states.items()},
    )


def _complete_if_needed(session, rule_id, evidence):
    if session.rule(rule_id).status != "done":
        _assert_armed(session, rule_id)
        _feed_no_alert(session, evidence)
    _assert_done(session, rule_id)


def _assert_alerted(events, rule_id):
    alerts = [event for event in events if event.type == "must_timeout"]
    assert any(event.rule_id == rule_id for event in alerts), (
        rule_id,
        [(event.rule_id, event.message, event.meta) for event in alerts],
    )


def _setup(*texts):
    def run(session):
        _feed_no_alert(session, *texts)

    return run


def _setup_product_behavior(session):
    _feed_no_alert(session, "你好欢迎", "我是销售顾问")
    _start_product_pitch(session)


def _setup_drive(session):
    _feed_no_alert(session, "你好欢迎", "准备试驾", "开始试驾")


def _setup_post_drive(session):
    _setup_drive(session)
    _feed_no_alert(session, "回到店")


def _setup_drive_review(session):
    _setup_post_drive(session)
    _feed_no_alert(session, "到了")


RULE_CASES = [
    ("confirm_appointment", _setup("你好欢迎"), "请问之前有预约吗", "0514:迎接准备:A2:询问预约/电话邀约"),
    ("confirm_intent", _setup("你好欢迎"), "您想看哪款车", "0514:迎接准备:A2:确认预约销售或意向车型"),
    ("self_intro", _setup("你好欢迎", "我来为您介绍"), "我是销售顾问张三", "0514:需求分析:B1:自我介绍"),
    ("invite_car", _setup_product_behavior, "坐进去上车感受一下", "0514:需求分析:B2:邀请客户上车体验"),
    ("ask_experience", _setup_product_behavior, "您之前开什么车", "0514:需求分析:B2:探寻用车经验/新能源接受度/关注点"),
    ("hint_test_drive", _setup("你好欢迎", "我是销售顾问", "给您介绍这款车", "多少钱"), "可以试驾体验动力", "0514:产品介绍:C1:高意向问题后顺势铺垫试驾"),
    ("invite_test_drive", _setup("你好欢迎", "我是销售顾问", "给您介绍这款车", "先回去考虑"), "安排试驾开一圈", "0514:产品介绍:C2:主动邀约试驾"),
    ("get_phone", _setup("你好欢迎", "我是销售顾问", "给您介绍配置", "加个微信"), "方便留个电话", "0514:留资开口:D1:离店前留电话"),
    ("try_wechat", _setup("你好欢迎", "我是销售顾问", "给您介绍配置", "留个联系方式", "不方便"), "加个微信", "0514:留资开口:D1:拒绝电话后加微信"),
    ("drive_route", _setup("你好欢迎", "准备试驾", "我们出去"), "这条路线大概十分钟", "0514:试乘试驾前:E1:试驾路线说明"),
    ("sign_agreement", _setup("你好欢迎", "准备试驾", "出发"), "先签试驾协议", "0514:试乘试驾前:E1:签署试驾协议"),
    ("phone_prepare", _setup("你好欢迎", "准备试驾", "车在外面"), "演示一键备车", "0514:试乘试驾前:E1:手机备车/解锁/安全带"),
    ("phone_connect", _setup("你好欢迎", "准备试驾", "起步"), "连接手机投屏", "0514:试乘试驾前:E1:手机互联投屏"),
    ("safe_stop", _setup("你好欢迎", "准备试驾", "开始试驾", "换手"), "靠边停注意后方", "0514:试乘试驾中:F2:换手前安全停车/注意后方"),
    ("adjust_seat", _setup("你好欢迎", "准备试驾", "开始试驾", "换手"), "调座椅后视镜", "0514:试乘试驾中:F2:客户接手前调座椅/方向盘/后视镜"),
    ("energy_recovery", _setup_drive, "这个是动能回收", "0514:试乘试驾中:F3:动能回收讲解"),
    ("guide_experience", _setup_drive, "您深踩一下体验加速", "0514:试乘试驾中:F3:引导客户体验加速/操作功能"),
    ("collect_feedback", _setup_drive, "感觉怎么样", "0514:试乘试驾中:F3:询问客户驾驶感受"),
    ("auto_park", _setup("你好欢迎", "准备试驾", "开始试驾", "回去吧"), "体验自动泊车", "0514:试乘试驾后:G1:自动泊车邀约"),
    ("drive_review", _setup_drive_review, "总结一下刚才体验", "0514:试乘试驾后:G1:试驾体验总结"),
    ("invite_in_store", _setup("你好欢迎", "准备试驾", "开始试驾", "回到店", "今天就到这"), "进店坐坐", "0514:试乘试驾后:G1:邀请客户进店洽谈"),
    ("get_phone_leave", _setup("你好欢迎", "先走了"), "留个电话", "0514:留资开口:D1:离店意图触发留电话"),
    ("invite_test_drive_retry", _start_retry_invite, "安排试驾", "0514:产品介绍:C2:拒绝或考虑后二次邀约试驾"),
]


def test_cases_cover_all_0514_must_rules():
    expected = {must["id"] for _, must in iter_must_rules(SOP_RULES)}
    actual = {rule_id for rule_id, _, _, _ in RULE_CASES}
    assert actual == expected
    refs = [excel_ref for _, _, _, excel_ref in RULE_CASES]
    assert all(ref.startswith("0514:") for ref in refs)
    assert len(set(refs)) == len(refs)


def test_all_0514_rules_can_be_completed_when_present():
    for rule_id, setup, evidence, _ in RULE_CASES:
        session = Session()
        setup(session)
        _assert_armed(session, rule_id)
        _feed_no_alert(session, evidence)
        assert session.rule(rule_id).status == "done", rule_id


def test_all_0514_rules_alert_when_missing_after_activation():
    for rule_id, setup, _, _ in RULE_CASES:
        session = Session()
        setup(session)
        _assert_armed(session, rule_id)
        _silence_other_rules(session, rule_id)
        _age_rule(session, rule_id)
        events = tick(None, session, SOP_RULES)
        _assert_alerted(events, rule_id)


def test_0514_realistic_full_flow_clean_path():
    session = Session()

    _feed_no_alert(session, "您好欢迎光临上汽 MG")
    _feed_no_alert(session, "您之前有预约吗")
    _assert_done(session, "confirm_appointment")
    _feed_no_alert(session, "今天主要想看哪台车 MG4 还是 MG7")
    _assert_done(session, "confirm_intent")

    _feed_no_alert(session, "我来为您介绍")
    _feed_no_alert(session, "我叫小王，是今天接待您的销售顾问")
    _assert_done(session, "self_intro")
    _start_product_pitch(session)
    _feed_no_alert(session, "您可以坐进去上车感受一下")
    _assert_done(session, "invite_car")
    _feed_no_alert(session, "您之前开什么车，平时主要家用还是通勤")
    _assert_done(session, "ask_experience")

    _feed_no_alert(session, "给您介绍这款车的动力和配置")
    _feed_no_alert(session, "动力怎么样多少钱")
    _assert_armed(session, "hint_test_drive")
    _feed_no_alert(session, "动力最好试驾体验一下")
    _assert_done(session, "hint_test_drive")
    _feed_no_alert(session, "那我们去坐坐算个价格")
    _complete_if_needed(session, "invite_test_drive", "可以先开一圈实际感受")

    _feed_no_alert(session, "加个微信")
    _assert_armed(session, "get_phone")
    _feed_no_alert(session, "方便留个电话吗")
    _assert_done(session, "get_phone")
    _feed_no_alert(session, "不方便")
    _complete_if_needed(session, "try_wechat", "那加个微信也可以")

    _feed_no_alert(session, "准备试驾")
    _feed_no_alert(session, "我们出去")
    _assert_armed(session, "drive_route")
    _assert_armed(session, "phone_prepare")
    _feed_no_alert(session, "这条路线大概十分钟")
    _assert_done(session, "drive_route")
    _feed_no_alert(session, "出发")
    _assert_armed(session, "sign_agreement")
    _assert_armed(session, "phone_connect")
    _feed_no_alert(session, "先签试驾协议")
    _assert_done(session, "sign_agreement")
    _feed_no_alert(session, "演示一键备车和安全带")
    _assert_done(session, "phone_prepare")
    _feed_no_alert(session, "连接手机投屏")
    _assert_done(session, "phone_connect")

    _feed_no_alert(session, "换手")
    _assert_armed(session, "safe_stop")
    _complete_if_needed(session, "adjust_seat", "先调座椅后视镜")
    _feed_no_alert(session, "靠边停注意后方")
    _assert_done(session, "safe_stop")
    _feed_no_alert(session, "这个是动能回收，松开电门有拖拽感")
    _assert_done(session, "energy_recovery")
    _feed_no_alert(session, "前面安全的话您深踩一下体验加速")
    _assert_done(session, "guide_experience")
    _feed_no_alert(session, "感觉怎么样，还习惯吗")
    _assert_done(session, "collect_feedback")

    _feed_no_alert(session, "回到店")
    _feed_no_alert(session, "回去吧")
    _assert_armed(session, "auto_park")
    _feed_no_alert(session, "到店前体验自动泊车")
    _assert_done(session, "auto_park")
    _feed_no_alert(session, "到了")
    _assert_armed(session, "drive_review")
    _feed_no_alert(session, "总结一下刚才体验")
    _assert_done(session, "drive_review")
    _feed_no_alert(session, "今天就到这")
    _assert_armed(session, "invite_in_store")
    _feed_no_alert(session, "进店坐坐，我给您算算配置和价格")
    _assert_done(session, "invite_in_store")


def test_0514_skip_to_test_drive_records_skipped_without_blocking():
    session = Session()
    events = _feed(session, "你好欢迎", "准备试驾")
    skipped = [event for event in events if event.type == "skipped"]
    assert session.stage_idx == 4
    assert skipped

    _feed(session, "我们出去")
    _assert_armed(session, "drive_route")


def test_0514_supported_real_wording_variants():
    cases = [
        ("confirm_appointment", _setup("你好欢迎"), "之前联系过吗"),
        ("confirm_intent", _setup("你好欢迎"), "今天主要想看 MG7"),
        ("drive_route", _setup("你好欢迎", "准备试驾", "我们出去"), "路线大概多久"),
        ("sign_agreement", _setup("你好欢迎", "准备试驾", "出发"), "电子协议签字"),
        ("phone_connect", _setup("你好欢迎", "准备试驾", "起步"), "连接手机投屏"),
        ("energy_recovery", _setup_drive, "松开电门会有拖拽感"),
    ]
    for rule_id, setup, evidence in cases:
        session = Session()
        setup(session)
        _assert_armed(session, rule_id)
        _feed_no_alert(session, evidence)
        _assert_done(session, rule_id)


REMEDY_CASES = [
    ("confirm_appointment", _setup("你好欢迎"), "您之前有预约吗"),
    ("self_intro", _setup("你好欢迎", "我来为您介绍"), "我是销售顾问张三"),
    ("sign_agreement", _setup("你好欢迎", "准备试驾", "出发"), "先签试驾协议"),
    ("energy_recovery", _setup_drive, "这个是动能回收"),
    ("invite_in_store", _setup("你好欢迎", "准备试驾", "开始试驾", "回到店", "今天就到这"), "进店坐坐"),
]


def test_0514_alert_then_remedy_representative_flow():
    for rule_id, setup, remedy in REMEDY_CASES:
        session = Session()
        setup(session)
        _assert_armed(session, rule_id)
        _silence_other_rules(session, rule_id)
        _age_rule(session, rule_id)
        _assert_alerted(tick(None, session, SOP_RULES), rule_id)
        _feed_no_alert(session, remedy)
        _assert_done(session, rule_id)


def test_0514_negative_context_and_customer_intent_do_not_false_alert():
    session = Session()
    _feed_no_alert(session, "你好欢迎", "我是销售顾问", "给您介绍这款车")
    _feed_no_alert(session, "动力怎么样")
    _assert_armed(session, "hint_test_drive")
    _feed_no_alert(session, "这个最好开起来感受，一会儿试驾一下")
    _assert_done(session, "hint_test_drive")

    session = Session()
    _setup_post_drive(session)
    _feed_no_alert(session, "今天就到这")
    _assert_armed(session, "invite_in_store")
    _feed_no_alert(session, "要不进店喝杯水，我再给您算一下配置和优惠")
    _assert_done(session, "invite_in_store")

    session = Session()
    _feed_no_alert(session, "你好欢迎", "你们是不是保证最低价")
    _feed_no_alert(session, "这个不能说保证最低价，我们只能按政策给您算")

    session = Session()
    _feed_no_alert(session, "你好欢迎", "我们不贬低其他品牌，主要看您自己的需求")


def _gap_phone_contact_variant_should_confirm_appointment():
    session = Session()
    _feed(session, "你好欢迎")
    _feed(session, "您是之前电话联系过的吗")
    _assert_done(session, "confirm_appointment")


def _gap_route_natural_duration_variant_should_match():
    session = Session()
    _feed(session, "你好欢迎", "准备试驾", "我们出去")
    _feed(session, "咱们这段路大概开十来分钟")
    _assert_done(session, "drive_route")


def _gap_agreement_scan_variant_should_match():
    session = Session()
    _feed(session, "你好欢迎", "准备试驾", "出发")
    _feed(session, "出发前先把试驾确认扫一下")
    _assert_done(session, "sign_agreement")


def _gap_phone_navigation_sync_variant_should_match():
    session = Session()
    _feed(session, "你好欢迎", "准备试驾", "起步")
    _feed(session, "可以把手机导航同步到车机上")
    _assert_done(session, "phone_connect")


def _gap_extra_product_evidence_layer_exists():
    session = Session()
    importlib.import_module("semantic_tagger")


EXPECTED_GAPS = [
    ("phone_contact_variant_should_confirm_appointment", _gap_phone_contact_variant_should_confirm_appointment),
    ("route_natural_duration_variant_should_match", _gap_route_natural_duration_variant_should_match),
    ("agreement_scan_variant_should_match", _gap_agreement_scan_variant_should_match),
    ("phone_navigation_sync_variant_should_match", _gap_phone_navigation_sync_variant_should_match),
    ("extra_product_evidence_layer_exists", _gap_extra_product_evidence_layer_exists),
]


def run_expected_gap_checks():
    resolved = []
    still_open = []
    for name, check in EXPECTED_GAPS:
        try:
            check()
        except (AssertionError, ModuleNotFoundError) as exc:
            still_open.append((name, exc.__class__.__name__))
        else:
            resolved.append(name)
    print(f"expected gaps open: {len(still_open)}")
    for name, kind in still_open:
        print(f"  open: {name} ({kind})")
    if resolved:
        print(f"expected gaps resolved: {len(resolved)}")
        for name in resolved:
            print(f"  resolved: {name}")


if __name__ == "__main__":
    test_cases_cover_all_0514_must_rules()
    test_all_0514_rules_can_be_completed_when_present()
    test_all_0514_rules_alert_when_missing_after_activation()
    test_0514_realistic_full_flow_clean_path()
    test_0514_alert_then_remedy_representative_flow()
    test_0514_skip_to_test_drive_records_skipped_without_blocking()
    test_0514_supported_real_wording_variants()
    test_0514_negative_context_and_customer_intent_do_not_false_alert()
    print("all strict 0514 SOP tests passed")
    run_expected_gap_checks()
