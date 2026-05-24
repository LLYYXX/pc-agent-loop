"""汽车销售接待SOP规则配置"""

SOP_RULES = {
    "stages": [
        {
            "id": "greeting",
            "name": "迎接问候",
            "hint_next": True,
            "enter_keywords": ["你好", "欢迎", "光临", "看车", "进来"],
            "reenter_keywords": [],
            "must": [
                {"id": "self_intro", "desc": "自我介绍", "keywords": ["我叫", "我是", "姓"], "alert": "报上姓名", "timeout_sec": 5}
            ],
            "forbidden": [
                {"id": "cold", "keywords": ["等一下", "我忙", "你先看"], "alert": "不要冷落客户", "cooldown_sec": 10}
            ]
        },
        {
            "id": "needs_analysis",
            "name": "需求分析",
            "hint_next": True,
            "enter_keywords": ["买车", "用途", "预算", "家用", "上下班", "第几辆"],
            "reenter_keywords": ["再想想", "重新考虑", "换个方向"],
            "must": [
                {"id": "ask_usage", "desc": "问用车场景", "keywords": ["用途", "用车", "场景", "上下班", "家用", "跑长途"], "alert": "问用车场景", "timeout_sec": 8},
                {"id": "ask_focus", "desc": "问关注点", "keywords": ["在意", "看重", "关注", "在乎", "要求"], "alert": "问关注点", "timeout_sec": 15},
                {"id": "ask_budget", "desc": "问预算", "keywords": ["预算", "价位", "多少钱", "价格范围"], "alert": "问预算", "timeout_sec": 22}
            ],
            "forbidden": [
                {"id": "push_expensive", "keywords": ["顶配", "最贵", "旗舰"], "negative": ["您喜欢", "您倾向", "您想要"], "alert": "别急着推贵车", "cooldown_sec": 10}
            ]
        },
        {
            "id": "product_intro",
            "name": "产品介绍",
            "hint_next": False,
            "enter_keywords": ["这款", "给您介绍", "配置", "发动机"],
            "reenter_keywords": ["再介绍", "再看看", "换一款", "再说说"],
            "must": [
                {"id": "walkaround", "desc": "六方位绕车", "keywords": ["车头", "车尾", "侧面", "轮毂", "内饰", "后备箱", "座椅"], "alert": "别忘了绕车介绍", "timeout_sec": 15},
                {"id": "selling_point", "desc": "核心卖点", "keywords": ["亮点", "优势", "领先", "独有", "同级最"], "alert": "说核心卖点", "timeout_sec": 25}
            ],
            "forbidden": [
                {"id": "bash_competitor", "keywords": ["垃圾", "不行", "太差", "别买那个"], "alert": "不要贬低竞品", "cooldown_sec": 10}
            ]
        },
        {
            "id": "test_drive",
            "name": "邀请试驾",
            "hint_next": False,
            "enter_keywords": ["试驾", "体验一下", "开一圈", "感受"],
            "reenter_keywords": [],
            "must": [],
            "forbidden": []
        },
        {
            "id": "negotiation",
            "name": "报价谈判",
            "hint_next": False,
            "enter_keywords": ["价格", "优惠", "折扣", "落地", "分期", "贷款"],
            "reenter_keywords": [],
            "must": [],
            "forbidden": [
                {"id": "false_promise", "keywords": ["保证最低", "绝对不会", "肯定没问题"], "alert": "不要虚假承诺", "cooldown_sec": 10}
            ]
        },
        {
            "id": "farewell",
            "name": "送别",
            "hint_next": False,
            "enter_keywords": ["再见", "回去考虑", "走了", "下次", "再来"],
            "reenter_keywords": [],
            "must": [
                {"id": "get_contact", "desc": "留联系方式", "keywords": ["微信", "电话", "手机号", "联系方式", "加个"], "alert": "留联系方式", "timeout_sec": 8},
                {"id": "send_off", "desc": "送客到门口", "keywords": ["送您", "慢走", "微信上聊", "线上聊"], "alert": "送客到门口", "timeout_sec": 15}
            ],
            "forbidden": []
        }
    ]
}
