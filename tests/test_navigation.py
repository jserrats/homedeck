from homedeck.ui.navigation import Action, ActionKind, layout_page


def _items(n):
    return [Action(ActionKind.ENTITY) for _ in range(n)]


def test_single_page_no_pagination():
    result = layout_page(_items(5), total_keys=32, fixed={}, page=0)
    assert len(result) == 5
    assert all(a.kind is ActionKind.ENTITY for a in result.values())
    assert set(result) == {0, 1, 2, 3, 4}


def test_fixed_back_key_reserved():
    fixed = {0: Action(ActionKind.BACK)}
    result = layout_page(_items(3), total_keys=32, fixed=fixed, page=0)
    assert result[0].kind is ActionKind.BACK
    # entities start at key 1, never overwriting the fixed key
    assert {k for k, a in result.items() if a.kind is ActionKind.ENTITY} == {1, 2, 3}


def test_pagination_first_page_has_next_only():
    # 40 items, 32 keys -> needs pagination; reserves last two free keys.
    result = layout_page(_items(40), total_keys=32, fixed={}, page=0)
    next_key, prev_key = 31, 30
    assert result[next_key].kind is ActionKind.PAGE and result[next_key].delta == 1
    assert prev_key not in result  # no Prev on first page
    entity_keys = [k for k, a in result.items() if a.kind is ActionKind.ENTITY]
    assert len(entity_keys) == 30  # 32 - 2 nav keys


def test_pagination_last_page_has_prev_only():
    result = layout_page(_items(40), total_keys=32, fixed={}, page=1)
    # 40 items, 30 per page -> 2 pages; last page holds remaining 10.
    assert result[30].kind is ActionKind.PAGE and result[30].delta == -1
    assert 31 not in result  # no Next on last page
    entity_keys = [k for k, a in result.items() if a.kind is ActionKind.ENTITY]
    assert len(entity_keys) == 10


def test_pagination_with_fixed_back():
    fixed = {0: Action(ActionKind.BACK)}
    # 31 free keys; 50 items overflow -> 29 entities per page + prev/next.
    result = layout_page(_items(50), total_keys=32, fixed=fixed, page=0)
    assert result[0].kind is ActionKind.BACK
    entity_keys = [k for k, a in result.items() if a.kind is ActionKind.ENTITY]
    assert len(entity_keys) == 29
    assert result[31].kind is ActionKind.PAGE  # Next


def test_page_clamped_to_range():
    # Asking for a page beyond the end clamps to the last page.
    high = layout_page(_items(40), total_keys=32, fixed={}, page=99)
    last = layout_page(_items(40), total_keys=32, fixed={}, page=1)
    assert high.keys() == last.keys()
