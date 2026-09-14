import os.path
import pickle
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QWidget
from qfluentwidgets import Action, Dialog, RoundMenu
from qfluentwidgets import FluentIcon as FIF

from shmtu_auth.src.config.project_directory import get_directory_data_path
from shmtu_auth.src.datatype.shmtu.auth.auth_user import (
    UserItem,
    insert_user_items,
    user_is_exist_in_list,
    user_list_move_down,
    user_list_move_to_bottom,
    user_list_move_to_top,
    user_list_move_up,
    user_list_select_list_by_index,
)
from shmtu_auth.src.gui.common.docker_config_generator import generate_machine_configs
from shmtu_auth.src.gui.view.components.custom.server_count_message_box import (
    ServerCountMessageBox,
)
from shmtu_auth.src.gui.view.components.custom.user_info_edit_widget import (
    UserInfoEditWidget,
)
from shmtu_auth.src.gui.view.components.custom.user_list_table import (
    UserListTableWidget,
)
from shmtu_auth.src.gui.view.components.fluent.widget_push_button import FPushButton
from shmtu_auth.src.gui.view.interface.gallery_interface import GalleryInterface
from shmtu_auth.src.gui.view.utils.gui_directory import navigate_to_file
from shmtu_auth.src.utils.logs import get_logger

logger = get_logger()

pickle_user_list_path = "user_list.pickle"
pickle_user_list_path = os.path.join(get_directory_data_path(), pickle_user_list_path)
# Log the path for debugging
logger.info(f"User list pickle path: {pickle_user_list_path}")


class UserListInterface(GalleryInterface):
    table_widget: UserListTableWidget
    user_info_edit_widget: UserInfoEditWidget

    user_list: List[UserItem]
    selected_index: List[int] = []

    # 上一次弹出过的右键菜单。每次右键都会新建一个，得有人负责回收。
    _context_menu: Optional[RoundMenu] = None

    def __init__(self, parent=None, user_list: List[UserItem] = None):
        super().__init__(
            title="校园网(统一认证平台)账号列表",
            subtitle="Author:Haomin Kong",
            parent=parent,
        )
        self.setObjectName("userListInterface")

        if user_list is None:
            raise Exception("user_list is None")

        self.user_list = user_list

        self.__init_widget()

        self.read_status()

        # 生成测试数据
        # self.user_list.clear()
        # self.user_list.extend(generate_test_user_list(20))
        self.table_widget.update_user_list()

    def __init_widget(self):
        user_info_widget = QWidget(self)
        user_info_layout = QHBoxLayout()

        # 表格
        self.table_widget = UserListTableWidget(self, user_list=self.user_list)
        self.table_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table_widget.customContextMenuRequested.connect(self.__show_context_menu)
        self.table_widget.slot_user_list_updated.connect(self.__user_list_updated)
        user_info_layout.addWidget(self.table_widget)

        # 编辑
        self.user_info_edit_widget = UserInfoEditWidget(
            parent=self, user_list=self.user_list, selected_index=self.selected_index
        )
        user_info_layout.addWidget(self.user_info_edit_widget)

        user_info_widget.setLayout(user_info_layout)
        self.vBoxLayout.addWidget(user_info_widget)

        button_generate_docker_config = FPushButton(self, "为服务器生成Docker配置")
        button_generate_docker_config.setFixedWidth(300)
        button_generate_docker_config.clicked.connect(self.__start_docker_generate)
        self.vBoxLayout.addWidget(button_generate_docker_config)

        self.table_widget.itemSelectionChanged.connect(self.__table_item_selected)
        self.__table_item_selected()

        self.user_info_edit_widget.onModifyButtonClick.connect(self.__on_user_modified)

    def read_status(self):
        if not os.path.exists(pickle_user_list_path):
            return

        try:
            with open(pickle_user_list_path, "rb") as f:
                user_list = pickle.load(f)

            self.user_list.clear()
            self.user_list.extend(user_list)

            logger.info(f"读取用户列表成功，共{len(user_list)}个用户")
        except Exception:
            return

        if self.user_list is not None:
            self.table_widget.update_user_list()

    def save_status(self):
        with open(pickle_user_list_path, "wb") as f:
            pickle.dump(self.user_list, f)

    def __user_list_updated(self):
        self.save_status()

    def __on_user_modified(self):
        """编辑区保存之后的回调：刷新表格，并保持选中刚编辑的那一行。

        ``update_user_list()`` 里的 ``setRowCount`` 会把选中状态清掉，
        新建场景下刚加的那一行会「保存完就丢了选中」，看着像没保存上。
        """
        self.table_widget.update_user_list()

        if len(self.selected_index) > 0:
            self.table_widget.set_select_index_list([self.selected_index[0]])

    def __table_item_selected(self):
        self.selected_index.clear()
        self.selected_index.extend(self.table_widget.selected_index)

        # 向编辑组件发送信号
        self.user_info_edit_widget.onSelectedItemChanged.emit()

    def __show_context_menu(self, pos):
        selected_items_count: int = self.table_widget.selected_items_count

        # 上一张菜单这时已经关掉了，交给事件循环回收 —— 每次右键都新建一张、
        # parent 又是本页，不主动回收就会一直攒着。
        if self._context_menu is not None:
            self._context_menu.close()
            self._context_menu.deleteLater()

        # 生成右键菜单
        menu = RoundMenu(parent=self)
        self._context_menu = menu

        action_create = Action(FIF.ADD, "新建")
        action_create.triggered.connect(self.__menu_action_create)
        menu.addAction(action_create)

        menu.addSeparator()

        action_clone = Action(FIF.COPY, "克隆")
        action_clone.triggered.connect(self.__menu_action_clone)
        menu.addAction(action_clone)
        action_del = Action(FIF.CLOSE, "删除")
        action_del.triggered.connect(self.__menu_action_del)
        menu.addAction(action_del)

        menu.addSeparator()

        action_move_to_top = Action(FIF.CARE_UP_SOLID, "移动到顶部")
        action_move_to_top.setEnabled(selected_items_count > 0)
        action_move_to_top.triggered.connect(self.__menu_action_move_to_top)
        menu.addAction(action_move_to_top)

        action_move_to_bottom = Action(FIF.CARE_DOWN_SOLID, "移动到底部")
        action_move_to_bottom.setEnabled(selected_items_count > 0)
        action_move_to_bottom.triggered.connect(self.__menu_action_move_to_bottom)
        menu.addAction(action_move_to_bottom)

        menu.addSeparator()

        action_move_up = Action(FIF.UP, "上移")
        action_move_up.setEnabled(selected_items_count > 0)
        action_move_up.triggered.connect(self.__menu_action_move_up)
        menu.addAction(action_move_up)

        action_move_down = Action(FIF.DOWN, "下移")
        action_move_down.setEnabled(selected_items_count > 0)
        action_move_down.triggered.connect(self.__menu_action_move_down)
        menu.addAction(action_move_down)

        menu.addSeparator()

        action_select_all = Action(FIF.ACCEPT, "全选")
        action_select_all.setEnabled(selected_items_count != self.table_widget.rowCount())
        action_select_all.triggered.connect(lambda: self.table_widget.selectAll())
        menu.addAction(action_select_all)

        action_select_cancel = Action(FIF.CANCEL, "取消选择")
        action_select_cancel.triggered.connect(lambda: self.table_widget.clearSelection())
        menu.addAction(action_select_cancel)

        menu.addSeparator()

        action_export_docker = Action(FIF.SEND_FILL, "导出Docker配置")
        action_export_docker.triggered.connect(self.__start_docker_generate)
        menu.addAction(action_export_docker)

        # 设置是否可用
        action_export_docker.setEnabled(self.table_widget.rowCount() > 0)

        have_selected_item = selected_items_count != 0

        action_clone.setEnabled(have_selected_item)
        action_del.setEnabled(have_selected_item)

        action_move_up.setEnabled(have_selected_item)
        action_move_down.setEnabled(have_selected_item)

        action_select_cancel.setEnabled(have_selected_item)

        # 右键菜单的坐标基准必须是**表格自己的视口**。
        #
        # customContextMenuRequested 给的 pos 是「表格视口内的局部坐标」，
        # 而这里原先写的是 self.mapToGlobal(pos)，self 是本页这个 ScrollArea ——
        # 等于把表格局部坐标当成滚动区局部坐标去换算，偏差正好是表格在滚动区里的
        # 位置（本页约 (36, 158)），而且窗口越往下滚偏差越大。
        # 表现就是：菜单整体偏左偏上，鼠标落在菜单的中间偏下，怎么滚都对不齐。
        global_pos = self.table_widget.viewport().mapToGlobal(pos)

        # 显示右键菜单
        menu.exec(global_pos, ani=True)

    def __start_docker_generate(self):
        w = ServerCountMessageBox(self.window())

        if not w.exec():
            return

        save_path = w.path_line_edit.text()
        machine_count = w.get_count()
        users_per_machine = w.get_user_count_per_server()

        result = self.__generate_docker_config(
            save_path=save_path,
            machine_count=machine_count,
            users_per_machine=users_per_machine,
        )

        if not result.ok:
            Dialog("生成失败", result.message, self.window()).exec()
            return

        d = Dialog(
            "生成成功",
            f"{result.message}\n\n目录：{save_path}\n\n是否打开该目录？",
            self.window(),
        )
        d.setContentCopyable(True)
        if d.exec():
            navigate_to_file(save_path)

    def __generate_docker_config(
        self,
        save_path: str = "",
        machine_count: int = 1,
        users_per_machine: int = 3,
    ):
        """为若干台服务器生成 Docker 部署配置。

        账号从界面这份列表里取有效的，按顺序切分，每台 ``users_per_machine`` 个；
        总数不够时后面的机器不生成（返回结果里会说明）。
        """
        valid_users = []
        for user_item in self.user_list:
            if user_item.is_valid():
                valid_users.append((user_item.user_id.strip(), user_item.password.strip()))

        return generate_machine_configs(
            save_path=save_path,
            users=valid_users,
            machine_count=machine_count,
            users_per_machine=users_per_machine,
        )

    def __add_item(self, user_item: List[UserItem], insert_index: int = -1):
        if len(user_item) == 0:
            return

        new_item_list: List[UserItem] = []
        for item in user_item:
            current_item = item.copy()
            if user_is_exist_in_list(
                user_list=self.user_list,
                user_id=current_item.user_id,
                excluded_indexes=[],
            ):
                current_item.user_id = ""

            new_item_list.append(current_item)

        first_index = insert_user_items(
            user_list=self.user_list,
            new_items=new_item_list,
            insert_index=insert_index,
        )

        self.table_widget.update_user_list()

        # 选中新增的第一行。
        #
        # 不这么做的话，新建之后没有任何选中项，右侧「用户信息编辑」会因为
        # setEnabled(len(selected_index) == 1) 保持置灰，于是空列表下新建的
        # 账号永远填不进学号密码 —— 用户被困在
        # 「没有账号 → 编辑区禁用 → 加不了账号」的死循环里。
        if first_index >= 0:
            self.table_widget.set_select_index_list([first_index])

    def __menu_action_create(self):
        insert_index = -1
        if self.table_widget.selected_items_count > 0:
            insert_index = self.table_widget.selected_index[self.table_widget.selected_items_count - 1] + 1

        self.__add_item([UserItem()], insert_index=insert_index)

    def __menu_action_clone(self):
        selected_list = user_list_select_list_by_index(user_list=self.user_list, index=self.table_widget.selected_index)

        insert_index = -1
        if self.table_widget.selected_items_count > 0:
            insert_index = self.table_widget.selected_index[self.table_widget.selected_items_count - 1] + 1

        self.__add_item(selected_list, insert_index=insert_index)

    def __menu_action_del(self):
        selected_index: List[int] = self.selected_index.copy()
        selected_index.sort(reverse=True)
        for i in selected_index:
            self.user_list.pop(i)

        self.table_widget.update_user_list()

    def __menu_action_move_to_top(self):
        final_selection_index: List[int] = user_list_move_to_top(user_list=self.user_list, index=self.selected_index)
        self.table_widget.update_user_list()
        self.table_widget.set_select_index_list(final_selection_index)

    def __menu_action_move_to_bottom(self):
        final_selection_index: List[int] = user_list_move_to_bottom(user_list=self.user_list, index=self.selected_index)
        self.table_widget.update_user_list()
        self.table_widget.set_select_index_list(final_selection_index)

    def __menu_action_move_up(self):
        final_selection_index: List[int] = user_list_move_up(user_list=self.user_list, index=self.selected_index, step=1)
        self.table_widget.update_user_list()
        self.table_widget.set_select_index_list(final_selection_index)

    def __menu_action_move_down(self):
        final_selection_index: List[int] = user_list_move_down(user_list=self.user_list, index=self.selected_index, step=1)
        self.table_widget.update_user_list()
        self.table_widget.set_select_index_list(final_selection_index)
