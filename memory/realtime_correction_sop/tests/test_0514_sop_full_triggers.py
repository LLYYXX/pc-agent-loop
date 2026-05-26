"""0514 SOP coverage.

- rule-level present/missing paths
- Excel SOP item to rule_id mapping
- MG7 26款 realistic clean flow
- alert then remedy flow
- skip-stage behavior
- wording variants
- SOP relation no-alert cases
- MG7 extra product evidence gap tracking
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
from match import session_log
from sop_rules import SOP_RULES

session_log.write = lambda _msg: None


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


def _assert_stage(session, stage_id):
    assert session.stage_idx >= 0, stage_id
    actual = SOP_RULES["stages"][session.stage_idx]["id"]
    assert actual == stage_id, (stage_id, actual, session.stage_idx)


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


def _assert_no_alert(events, rule_id):
    alerts = [event for event in events if event.type == "must_timeout" and event.rule_id == rule_id]
    assert not alerts, [(event.rule_id, event.message, event.meta) for event in alerts]


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


MG7_SHOWROOM_EVIDENCE_CASES = [
    (
        "mg7_model_overview",
        "MG 是全球最早量产轿跑车型的品牌，MG7 作为 B 级运动轿车新标杆，集无框车门、三段式电动尾翼和智能座舱于一身。",
    ),
    (
        "mg7_luxury_equipment",
        "MG7 全系标配三段式电动尾翼、8155 智能座舱、阿里 AI 大模型、全场景手车互联、自定义场景魔方和 BOSE 音响系统。",
    ),
    ("mg7_rear_wing", "MG7 的三段式电动尾翼速度起来后打开，也能增强行驶稳定性。"),
    ("mg7_sport_control", "X-mode 超级玩家模式可以自定义动力、转向、电磁悬架和运动尾排。"),
    ("mg7_magic_scene", "自定义场景魔方可以把座舱、音乐、灯光和驾驶模式组合成常用场景。"),
    ("mg7_bose_light", "BOSE 音响系统搭配 256 色氛围灯，车内体验更有沉浸感。"),
    ("mg7_seat_heat_vent", "座椅通风和加热是 MG7 的豪华配置点。"),
    ("mg7_l2_assist", "MG7 具备 L2 安全驾驶辅助，可以减轻日常驾驶疲劳。"),
    ("mg7_warranty", "双保权益可以降低后续用车顾虑。"),
    ("mg7_powertrain", "MG7 搭载 2.0T 加 9AT 双十佳动力总成。"),
]


MG7_TEST_DRIVE_EVIDENCE_CASES = [
    ("mg7_remote_parking", "MG7 搭载遥控泊出和记忆泊车功能，可以让车辆从停车位自动泊出。"),
    ("mg7_ngp", "我们先设置一条带高架的试驾路线，MG7 搭载 NGP 高阶智能辅助驾驶。"),
    ("mg7_drive_scene_magic", "试驾中可以通过场景魔方切换适合当前路况的驾驶体验。"),
    ("mg7_acceleration_0_60", "进入 Super Sport 模式后，您可以感受一下 0 到 60 公里的加速体验。"),
    ("mg7_acc_follow", "在市区拥堵路段，开启辅助驾驶后，车辆可以自动跟车、自动停下并跟随前车启动。"),
    ("mg7_auto_lane_change", "相邻车道安全时，轻拨转向杆，车辆可以完成自动变道。"),
    ("mg7_elsd_mcdc_cornering", "这个弯道可以感受 MG7 的 E-LSD 电子限滑差速器和 mCDC 智能电控悬架。"),
    ("mg7_bumpy_road_filter", "前方路段比较颠簸，MG7 的 mCDC 悬架可以根据路况调节，提升舒适性。"),
    ("mg7_auto_park", "到达门店前停车位了，我邀请您体验一下智能泊车功能。"),
]


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

    _feed_no_alert(session, "我是销售顾问小王，今天由我来接待您")
    _assert_stage(session, "self_intro")
    _feed_no_alert(session, "我叫小王，是今天接待您的销售顾问")
    _assert_done(session, "self_intro")
    _start_product_pitch(session)
    _feed_no_alert(session, "您可以坐进去上车感受一下")
    _assert_done(session, "invite_car")
    _feed_no_alert(session, "您之前开什么车，平时主要家用还是通勤，有什么需求")
    _assert_done(session, "ask_experience")
    _feed_no_alert(session, "那我结合您的需求，给您介绍 MG7 的空间、动力和智能座舱")
    _assert_stage(session, "hint_test_drive")

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


def test_0514_mg7_realistic_full_flow_clean_path():
    test_0514_realistic_full_flow_clean_path()


def test_0514_skip_to_test_drive_records_skipped_without_blocking():
    session = Session()
    events = _feed(session, "你好欢迎", "准备试驾")
    skipped = [event for event in events if event.type == "skipped"]
    _assert_stage(session, "drive_route")
    assert skipped

    _feed(session, "我们出去")
    _assert_armed(session, "drive_route")


def test_0514_needs_product_contact_stage_enter_reenter_and_hit():
    session = Session()

    _feed_no_alert(session, "你好欢迎")
    _feed_no_alert(session, "您的需求是什么")
    _assert_stage(session, "self_intro")
    _assert_armed(session, "self_intro")
    _feed_no_alert(session, "我是销售顾问小王")
    _assert_done(session, "self_intro")

    _feed_no_alert(session, "给您介绍 MG7 的空间、动力和智能座舱")
    _assert_stage(session, "hint_test_drive")
    _feed_no_alert(session, "动力怎么样多少钱")
    _assert_armed(session, "hint_test_drive")
    _feed_no_alert(session, "体验动力会更直观")
    _assert_done(session, "hint_test_drive")
    _feed_no_alert(session, "那我们去坐坐算个价格")
    _assert_armed(session, "invite_test_drive")
    _feed_no_alert(session, "可以开一圈")
    _assert_done(session, "invite_test_drive")

    _feed_no_alert(session, "加个微信")
    _assert_stage(session, "get_contact")
    _assert_armed(session, "get_phone")
    _feed_no_alert(session, "方便留个电话")
    _assert_done(session, "get_phone")
    _feed_no_alert(session, "不方便")
    _assert_armed(session, "try_wechat")
    _feed_no_alert(session, "那我们加个微信")
    _assert_done(session, "try_wechat")

    _feed_no_alert(session, "再介绍一下配置")
    _assert_stage(session, "hint_test_drive")
    _feed_no_alert(session, "再想想需求")
    _assert_stage(session, "self_intro")


def test_0514_supported_real_wording_variants():
    cases = [
        ("confirm_appointment", _setup("你好欢迎"), "之前联系过吗"),
        ("confirm_intent", _setup("你好欢迎"), "今天主要想看 MG7"),
        ("drive_route", _setup("你好欢迎", "准备试驾", "我们出去"), "路线大概多久"),
        ("sign_agreement", _setup("你好欢迎", "准备试驾", "出发"), "电子协议签字"),
        ("phone_connect", _setup("你好欢迎", "准备试驾", "起步"), "连接手机投屏"),
        ("energy_recovery", _setup_drive, "松开电门会有拖拽感"),
        ("ask_experience", _setup_product_behavior, "您之前开什么车，平时主要什么用途"),
        ("ask_experience", _setup_product_behavior, "这次买车主要有什么要求，之前开过什么车"),
        ("ask_experience", _setup_product_behavior, "您比较关注空间动力还是智能配置，之前对比过什么车"),
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
    ("ask_experience", _setup_product_behavior, "您之前开什么车，平时主要家用还是通勤，有什么需求"),
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


def test_0514_sop_relation_no_alert_cases():
    session = Session()
    _feed_no_alert(session, "你好欢迎", "我是销售顾问", "给您介绍这款车")
    _feed_no_alert(session, "动力怎么样多少钱")
    _assert_armed(session, "hint_test_drive")
    _feed_no_alert(session, "这个最好开起来感受，一会儿试驾一下")
    _assert_done(session, "hint_test_drive")
    _feed_no_alert(session, "那我们去坐坐算个价格")
    _assert_done(session, "invite_test_drive")
    _age_rule(session, "invite_test_drive")
    _assert_no_alert(tick(None, session, SOP_RULES), "invite_test_drive")

    session = Session()
    _feed_no_alert(session, "你好欢迎", "准备试驾")
    _feed_no_alert(session, "先签试驾协议")
    _feed_no_alert(session, "出发")
    _assert_done(session, "sign_agreement")
    _age_rule(session, "sign_agreement")
    _assert_no_alert(tick(None, session, SOP_RULES), "sign_agreement")

    session = Session()
    _feed_no_alert(session, "你好欢迎", "加个微信")
    _feed_no_alert(session, "方便留个电话")
    _assert_done(session, "get_phone")
    _feed_no_alert(session, "不方便")
    _feed_no_alert(session, "那我们加个微信，我把介绍视频发给您")
    _assert_done(session, "try_wechat")
    _age_rule(session, "try_wechat")
    _assert_no_alert(tick(None, session, SOP_RULES), "try_wechat")

    session = Session()
    _setup_post_drive(session)
    _feed_no_alert(session, "今天就到这")
    _assert_armed(session, "invite_in_store")
    _feed_no_alert(session, "现在进店喝杯饮品，我们详细聊聊后续购车方案和优惠政策吧")
    _assert_done(session, "invite_in_store")
    _age_rule(session, "invite_in_store")
    _assert_no_alert(tick(None, session, SOP_RULES), "invite_in_store")


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


def _semantic_tags(text):
    semantic_tagger = importlib.import_module("semantic_tagger")
    if hasattr(semantic_tagger, "tag_text"):
        return set(semantic_tagger.tag_text(text))
    if hasattr(semantic_tagger, "tag"):
        return set(semantic_tagger.tag(text))
    raise AssertionError("semantic_tagger needs tag_text(text) or tag(text)")


def _gap_mg7_showroom_product_evidence_layer_exists():
    for evidence_id, text in MG7_SHOWROOM_EVIDENCE_CASES:
        assert evidence_id in _semantic_tags(text), evidence_id


def _gap_mg7_test_drive_product_evidence_layer_exists():
    for evidence_id, text in MG7_TEST_DRIVE_EVIDENCE_CASES:
        assert evidence_id in _semantic_tags(text), evidence_id


EXPECTED_GAPS = [
    ("phone_contact_variant_should_confirm_appointment", _gap_phone_contact_variant_should_confirm_appointment),
    ("route_natural_duration_variant_should_match", _gap_route_natural_duration_variant_should_match),
    ("agreement_scan_variant_should_match", _gap_agreement_scan_variant_should_match),
    ("phone_navigation_sync_variant_should_match", _gap_phone_navigation_sync_variant_should_match),
    ("mg7_showroom_product_evidence_layer_exists", _gap_mg7_showroom_product_evidence_layer_exists),
    ("mg7_test_drive_product_evidence_layer_exists", _gap_mg7_test_drive_product_evidence_layer_exists),
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
    test_0514_mg7_realistic_full_flow_clean_path()
    test_0514_alert_then_remedy_representative_flow()
    test_0514_skip_to_test_drive_records_skipped_without_blocking()
    test_0514_needs_product_contact_stage_enter_reenter_and_hit()
    test_0514_supported_real_wording_variants()
    test_0514_sop_relation_no_alert_cases()
    print("all strict 0514 SOP tests passed")
    run_expected_gap_checks()
