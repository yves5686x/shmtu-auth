import os
import sys

from shmtu_auth.src.config.data_directory import get_data_path

project_name = "shmtu_auth"

py_mode = True
gui_mode = False


def get_current_py_path() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def get_pyfile_base_path() -> str:
    """源码运行时的基准目录（仓库根）。

    从本文件所在目录往上退 4 层：config → src → shmtu_auth → src → 仓库根。
    注意这里假设了源码目录的固定层级，包被安装到 site-packages 后不再成立。
    """
    current_dir_path = get_current_py_path()

    for _ in range(4):
        current_dir_path = os.path.dirname(current_dir_path)

    return current_dir_path


def get_running_directory() -> str:
    return os.getcwd()


def get_directory_base_path() -> str:
    # PyInstaller 打包后 __file__ 在临时解压目录（onefile 每次启动还会换），
    # 往上退 4 层找到的是 Temp 里的随机目录 —— 日志 / 用户数据写进去，
    # 进程一退出就没了。冻结运行时一律以 exe 所在目录为基准，数据才能留存。
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))

    if gui_mode:
        return get_data_path(project_name)

    if py_mode:
        return get_pyfile_base_path()

    # 两个模式都没开时退回当前工作目录，避免返回 None 让下游 join 报错。
    return get_running_directory()


def get_directory_child(child_name: str) -> str:
    base_path = get_directory_base_path()
    final_path = os.path.join(base_path, child_name)

    os.makedirs(final_path, exist_ok=True)

    return final_path


def get_directory_config_path():
    return get_directory_child("config")


def get_directory_data_path():
    return get_directory_child("data")


def get_directory_log_path():
    return get_directory_child("logs")


if __name__ == "__main__":
    print("Py Mode:", py_mode)
    print("GUI Mode:", gui_mode)
    print("Running Directory:", get_running_directory())
    print(get_directory_base_path())
    print(get_directory_config_path())
    print(get_directory_data_path())
    print(get_directory_log_path())
