import csv
import datetime
import os.path
import pickle
from typing import List

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QHBoxLayout, QTableWidgetItem, QWidget
from qfluentwidgets import InfoBar, InfoBarIcon, InfoBarPosition, TableWidget

from shmtu_auth.src.config.project_directory import get_directory_data_path
from shmtu_auth.src.gui.common.signal_bus import signal_bus
from shmtu_auth.src.gui.view.components.fluent.widget_push_button import FPushButton
from shmtu_auth.src.gui.view.interface.gallery_interface import GalleryInterface
from shmtu_auth.src.utils.logs import get_logger

logger = get_logger()

pickle_log_path = "logs.pickle"
pickle_log_path = os.path.join(get_directory_data_path(), pickle_log_path)

# 工作日志只保留最近这么多条；超了就丢最老的。
# 原先条数不设上限，且每来一行日志都要重排两遍列宽 + 把整份记录重新 pickle 落盘，
# 单行开销随历史线性增长 —— 日志一多界面就明显卡顿，且越用越卡。
MAX_LOG_RECORDS = 800
# 超过上限后一次裁到这么多，避免「每多一行就重建整张表」
LOG_TRIM_TARGET = 600
# 「重排列宽 + 落盘」的防抖窗口（毫秒）：一段时间内的多行日志合并成一次
LOG_FLUSH_DELAY_MS = 400


class LogInterface(GalleryInterface):
    def __init__(self, parent=None):
        super().__init__(title="程序日志", subtitle="Author:Haomin Kong", parent=parent)
        self.setObjectName("logInterface")

        info_bar = InfoBar(
            icon=InfoBarIcon.INFORMATION,
            title="提示",
            content="这里是程序的工作日志，仅用于反馈。非专业人员请忽略~",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            duration=-1,
            position=InfoBarPosition.NONE,
            parent=self,
        )
        self.vBoxLayout.addWidget(info_bar)

        # 创建按钮容器
        button_widget = QWidget()
        button_layout = QHBoxLayout(button_widget)
        button_layout.setContentsMargins(0, 0, 0, 0)

        button_save_log = FPushButton(self, "导出日志")
        button_save_log.setFixedWidth(100)
        button_save_log.clicked.connect(self.export_logs)

        button_clear_log = FPushButton(self, "清空日志")
        button_clear_log.setFixedWidth(100)
        button_clear_log.clicked.connect(self.clear_logs)

        button_layout.addWidget(button_save_log)
        button_layout.addWidget(button_clear_log)
        button_layout.addStretch()  # 添加弹性空间，让按钮靠左对齐

        self.vBoxLayout.addWidget(button_widget)

        self.logTable = LogTableFrame(self)
        self.vBoxLayout.addWidget(self.logTable)

    def add_new_record(self, time: str = "", event: str = "", status: str = ""):
        self.logTable.add_record(time=time, event=event, status=status)

    def export_logs(self):
        """导出日志到文件"""
        logger.info("用户点击导出日志按钮")

        # 检查是否有日志数据
        if not self.logTable.record_list:
            InfoBar.warning(title="无数据", content="当前没有日志记录可导出", duration=2000, parent=self)
            return

        # 打开文件保存对话框
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出日志",
            f"shmtu_auth_logs_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            "CSV文件 (*.csv);;文本文件 (*.txt);;所有文件 (*.*)",
        )

        if not file_path:
            logger.info("用户取消导出日志")
            return

        try:
            # 根据文件扩展名选择导出格式
            if file_path.lower().endswith(".csv"):
                # 导出为CSV格式
                with open(file_path, "w", newline="", encoding="utf-8-sig") as csvfile:
                    writer = csv.writer(csvfile)
                    # 写入表头
                    writer.writerow(["日志时间", "事件", "状态"])
                    # 写入数据
                    for record in self.logTable.record_list:
                        writer.writerow(record)
            else:
                # 导出为文本格式
                with open(file_path, "w", encoding="utf-8") as txtfile:
                    txtfile.write("SHMTU Auth 日志导出\n")
                    txtfile.write("=" * 50 + "\n")
                    txtfile.write(f"导出时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                    txtfile.write(f"总记录数: {len(self.logTable.record_list)}\n")
                    txtfile.write("=" * 50 + "\n\n")

                    for record in self.logTable.record_list:
                        txtfile.write(f"时间: {record[0]}\n")
                        txtfile.write(f"事件: {record[1]}\n")
                        txtfile.write(f"状态: {record[2]}\n")
                        txtfile.write("-" * 30 + "\n")

            logger.info(f"日志导出成功: {file_path}")
            InfoBar.success(
                title="导出成功", content=f"日志已成功导出到: {os.path.basename(file_path)}", duration=3000, parent=self
            )

        except Exception as e:
            logger.error(f"导出日志失败: {str(e)}")
            InfoBar.error(title="导出失败", content=f"导出过程中发生错误: {str(e)}", duration=4000, parent=self)

    def clear_logs(self):
        """清空日志功能"""
        # 显示确认对话框
        from qfluentwidgets import MessageBox

        title = "清空日志"
        content = "确定要清空所有日志记录吗？此操作不可撤销！"

        w = MessageBox(title, content, self)
        if w.exec():
            logger.info("用户确认清空日志")
            # 清空日志表格
            self.logTable.clear_all_logs()

            # 显示成功提示
            InfoBar.success(title="清空成功", content="所有日志记录已清空", duration=2000, parent=self)
        else:
            logger.info("用户取消清空日志")


class LogTableFrame(TableWidget):
    column_count = 3

    record_count = 0
    record_list: List[List[str]] = []

    def __init__(self, parent=None):
        super().__init__(parent)

        # 两个都是可变对象，必须是实例属性（写成类属性会让所有实例共享同一份）
        self.record_list = []
        self.record_count = 0

        self.verticalHeader().hide()
        self.setBorderRadius(8)
        self.setBorderVisible(True)

        self.setColumnCount(self.column_count)
        self.setHorizontalHeaderLabels(
            [
                "日志时间",
                "事件",
                "状态",
            ]
        )

        # self.add_record("2024年01月01日 12:34:56", "检测到网络断开", "成功")

        self.resizeColumnsToContents()

        # 禁止直接编辑
        self.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)

        # 重排列宽 + 落盘合并到一次（见 __schedule_flush）
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(LOG_FLUSH_DELAY_MS)
        self._flush_timer.timeout.connect(self.__flush)

        # 退出前把还没落盘的部分写掉
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.flush)

        if os.path.exists(pickle_log_path):
            self.read_status()

        signal_bus.signal_log_new.connect(self.add_new_record)

    def __schedule_flush(self):
        """把「重排列宽 + 落盘」推迟到一小段空闲之后，多行日志只做一次。"""
        self._flush_timer.start()

    def __flush(self):
        self.resizeColumnsToContents()
        self.save_status()

    def flush(self):
        """立即把待处理的重排与落盘做掉（清空、退出前调用）。"""
        self._flush_timer.stop()
        self.__flush()

    def add_new_record(self, event: str, status: str):
        now = datetime.datetime.now()
        time = now.strftime("%Y-%m-%d %H:%M:%S")
        self.add_record(time=time, event=event, status=status)

    def read_status(self):
        if not os.path.exists(pickle_log_path):
            return

        try:
            with open(pickle_log_path, "rb") as f:
                self.record_list = pickle.load(f)
        except Exception:
            return

        if self.record_list is None:
            self.record_list = []
            return

        # 历史上这份记录没有上限，可能已经攒了几千条 —— 启动时就先裁掉，避免一开机就卡
        if len(self.record_list) > MAX_LOG_RECORDS:
            logger.info(f"日志记录过多（{len(self.record_list)} 条），只保留最近 {MAX_LOG_RECORDS} 条")
            del self.record_list[: len(self.record_list) - MAX_LOG_RECORDS]

        self.update_by_list()

    def save_status(self):
        try:
            with open(pickle_log_path, "wb") as f:
                pickle.dump(self.record_list, f)
        except Exception as e:
            # 落盘失败不该影响界面（这个调用现在位于定时器回调里）
            logger.warning(f"保存日志记录失败: {e}")

    def update_record(self, index: int, current_record: List[str]):
        for j in range(min(current_record.__len__(), self.column_count)):
            current_text = current_record[j]
            self.setItem(index, j, QTableWidgetItem(current_text))

    def update_by_list(self, record_list: List[List[str]] = None):
        if record_list is not None:
            self.record_list = record_list.copy()

        self.record_count = self.record_list.__len__()

        self.setRowCount(self.record_count)

        for i, record_item in enumerate(self.record_list):
            self.update_record(i, record_item)

        self.resizeColumnsToContents()
        self.save_status()

    def add_record(self, time: str = "", event: str = "", status: str = ""):
        self.record_list.append([time, event, status])
        self.record_count = len(self.record_list)

        if self.record_count > MAX_LOG_RECORDS:
            # 一次裁掉一批（而不是每多一行裁一行），否则之后每来一行都要重建整张表
            del self.record_list[: self.record_count - LOG_TRIM_TARGET]
            self.record_count = len(self.record_list)
            logger.info(f"日志超过 {MAX_LOG_RECORDS} 条，已裁到最近 {self.record_count} 条")
            self.update_by_list()
            return

        # 只追加一行，不做整表重建
        self.setRowCount(self.record_count)
        self.update_record(self.record_count - 1, self.record_list[-1])
        logger.info(f"添加日志记录：{self.record_list[-1]}")

        # 重排列宽和落盘都推迟合并执行（原先这里要 resize 两遍 + 把整份记录重写一遍）
        self.__schedule_flush()

    def clear_all_logs(self):
        """清空所有日志记录"""
        logger.info("开始清空所有日志记录")

        # 清空记录列表
        self.record_list = []
        self.record_count = 0

        # 清空表格显示
        self.setRowCount(0)

        # 清空后立刻落盘，别让之前排队的刷新再写一次
        self.flush()

        logger.info("所有日志记录已清空")
