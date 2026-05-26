def iter_must_rules(rules: dict):
    for stage in rules["stages"]:
        for must in stage.get("must", []):
            yield stage, must
    for must in rules.get("insert_rules", []):
        yield None, must


def audio_id(rule: dict) -> str:
    return rule.get("audio_id", rule["id"])
