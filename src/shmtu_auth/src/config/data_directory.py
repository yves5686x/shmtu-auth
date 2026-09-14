import os
import sys


def get_windows_data_path(project_name=""):
    data_path = os.path.join(os.environ["USERPROFILE"], "AppData", "Roaming")

    project_path = project_name.strip()
    if len(project_path):
        data_path = os.path.join(data_path, project_path)

    os.makedirs(data_path, exist_ok=True)

    return data_path


def get_mac_data_path(project_name=""):
    data_path = os.path.join(os.environ["HOME"], "Library", "Application Support")

    project_path = project_name.strip()
    if len(project_path):
        data_path = os.path.join(data_path, project_path)

    os.makedirs(data_path, exist_ok=True)

    return data_path


def get_linux_data_path(project_name=""):
    data_path = os.path.join(os.environ["HOME"], ".config")

    project_path = project_name.strip()
    if len(project_path):
        data_path = os.path.join(data_path, project_path)

    os.makedirs(data_path, exist_ok=True)

    return data_path


def get_data_path(project_name=""):
    # macOS 的 os.name 也是 "posix"，所以必须先按 sys.platform 判 darwin，
    # 否则永远落进下面的 posix 分支，macOS 会拿到 ~/.config 而不是
    # ~/Library/Application Support。
    if sys.platform == "darwin":
        return get_mac_data_path(project_name)
    elif os.name == "nt":
        return get_windows_data_path(project_name)
    elif os.name == "posix":
        return get_linux_data_path(project_name)
    else:
        raise Exception("Unsupported OS")


if __name__ == "__main__":
    print("os.name:", os.name)
    print("sys.platform:", sys.platform)
    # 只演示当前平台真正会用的那个：直接调其余平台的分支会因为取不到
    # USERPROFILE 之类的环境变量而抛 KeyError。
    print(get_data_path("shmtu_auth"))
