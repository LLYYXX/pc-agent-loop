def any_kw_in(text: str, keywords: list) -> bool:
    if not text or not keywords:
        return False
    return any(kw in text for kw in keywords)


def match_window(full_text: str, keywords: list, window: int = 200) -> bool:
    if not full_text:
        return False
    return any_kw_in(full_text[-window:] if window else full_text, keywords)
