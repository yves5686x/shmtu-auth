"""用户列表增删的纯逻辑测试。

这里只测 ``insert_user_items`` —— 界面「新建 / 克隆」之后要选中哪一行全靠它的
返回值。这个环境装不了 PySide6，GUI 组件没法实例化，所以把这段逻辑从界面里
抽成纯函数单独守护。

背景：以前新建完不选中任何行，右侧「用户信息编辑」因为
``setEnabled(len(selected_index) == 1)`` 一直置灰，空列表下新建的账号
永远填不进学号密码，用户被困在「没有账号 → 编辑区禁用 → 加不了账号」里。
"""

import datetime

from shmtu_auth.src.datatype.shmtu.auth.auth_user import (
    UserItem,
    insert_user_items,
)


def make_user(user_id: str) -> UserItem:
    return UserItem(
        user_id=user_id,
        user_name=f"name_{user_id}",
        password="pwd",
        expire_date=datetime.date(2030, 1, 1),
    )


class TestInsertUserItems:
    def test_append_to_empty_list_returns_zero(self):
        """空列表下新建：第一个（也是唯一）新增项在第 0 行。

        这是「加不了第一个账号」那条 bug 的正面用例 —— 必须返回 0，
        界面选中第 0 行后编辑区才会启用。
        """
        user_list: list = []

        first_index = insert_user_items(user_list, [make_user("202500000001")])

        assert first_index == 0
        assert len(user_list) == 1
        assert user_list[0].user_id == "202500000001"

    def test_append_to_non_empty_list_returns_old_length(self):
        user_list = [make_user("202500000001"), make_user("202500000002")]

        first_index = insert_user_items(user_list, [make_user("202500000003")])

        assert first_index == 2
        assert len(user_list) == 3
        assert user_list[2].user_id == "202500000003"

    def test_insert_at_head(self):
        user_list = [make_user("202500000001")]

        first_index = insert_user_items(user_list, [make_user("202500000000")], 0)

        assert first_index == 0
        assert user_list[0].user_id == "202500000000"
        assert user_list[1].user_id == "202500000001"

    def test_insert_in_middle(self):
        user_list = [make_user("202500000001"), make_user("202500000003")]

        first_index = insert_user_items(user_list, [make_user("202500000002")], 1)

        assert first_index == 1
        assert [u.user_id for u in user_list] == [
            "202500000001",
            "202500000002",
            "202500000003",
        ]

    def test_insert_multiple_keeps_order_and_returns_first(self):
        """克隆多个时返回第一个新增项的下标，且顺序不乱。"""
        user_list = [make_user("202500000001")]

        new_items = [make_user("202500000002"), make_user("202500000003")]
        first_index = insert_user_items(user_list, new_items, 1)

        assert first_index == 1
        assert [u.user_id for u in user_list] == [
            "202500000001",
            "202500000002",
            "202500000003",
        ]

    def test_negative_index_means_append(self):
        """界面用 -1 表示「追加到末尾」，-2 之类的也应同样处理。"""
        user_list = [make_user("202500000001")]

        first_index = insert_user_items(user_list, [make_user("202500000002")], -1)

        assert first_index == 1
        assert user_list[1].user_id == "202500000002"

    def test_empty_new_items_is_noop(self):
        user_list = [make_user("202500000001")]

        first_index = insert_user_items(user_list, [])

        assert first_index == -1
        assert len(user_list) == 1

    def test_newly_created_user_becomes_valid_after_filling(self):
        """新建出来的空账号，填完 12 位学号和密码后应当判定为有效。

        否则用户填完了却在认证时被跳过，等于白填。
        """
        blank = UserItem()
        assert not blank.is_valid()

        blank.user_id = "202500000001"
        blank.password = "some_password"

        assert blank.is_valid()

    def test_blank_user_has_future_expire_date(self):
        """新建账号的过期时间不能是已过去的，否则一填完就是「已过期」。"""
        blank = UserItem()

        assert blank.expire_date > datetime.date.today()
