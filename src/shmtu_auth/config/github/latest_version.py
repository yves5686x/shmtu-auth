from __future__ import annotations

import re

import requests

github_author_name = "a645162"
github_repo_name = "shmtu-auth"

DEFAULT_BRANCHES = ["main", "beta", "dev"]


def get_github_branches(fetch_online: bool = False, timeout: float = 3.0) -> list[str]:
    """获取GitHub仓库的分支列表，排除gh-pages分支。

    默认完全离线（fetch_online=False），直接返回预设分支列表 ['main', 'beta', 'dev']，
    避免在主 UI 线程或启动阶段发起实时网络请求导致程序卡顿。
    仅当明确指定 fetch_online=True 时才联网获取，并带有超时限制。
    """
    if not fetch_online:
        return DEFAULT_BRANCHES.copy()

    url = f"https://api.github.com/repos/{github_author_name}/{github_repo_name}/branches"

    try:
        response = requests.get(url, timeout=timeout)
        if response.status_code != 200:
            return DEFAULT_BRANCHES.copy()

        branches_data = response.json()
        branches = []

        for branch_info in branches_data:
            branch_name = branch_info.get("name", "")
            # 排除gh-pages分支
            if branch_name and branch_name != "gh-pages":
                branches.append(branch_name)

        # 确保main分支在列表中，如果不在则添加
        if "main" not in branches and len(branches) > 0:
            branches.insert(0, "main")
        elif len(branches) == 0:
            branches = DEFAULT_BRANCHES.copy()

        # 将main分支排到第一位
        if "main" in branches:
            branches.remove("main")
            branches.insert(0, "main")

        return branches
    except Exception:
        return DEFAULT_BRANCHES.copy()


def get_branch_version(branch: str = "main", timeout: float = 3.0) -> str:
    url = f"https://raw.githubusercontent.com/{github_author_name}/{github_repo_name}"
    url += f"/refs/heads/{branch}/"
    url += "src/shmtu_auth/version.py"

    try:
        response = requests.get(url, timeout=timeout)
        content = response.text.strip()
        if len(content) == 0:
            return ""

        # 使用正则表达式匹配 __version__ 变量
        version_pattern = r'__version__\s*=\s*["\']([^"\']+)["\']'
        match = re.search(version_pattern, content)

        if not match:
            return ""

        version_str = match.group(1)

        spilt_list = version_str.split(".")
        if len(spilt_list) != 3:
            return ""
        for i in spilt_list:
            if not i.isdigit():
                return ""

        return version_str
    except Exception:
        return ""


if __name__ == "__main__":
    print("Available branches:", get_github_branches())
    print("main", get_branch_version("main"))
    print("beta", get_branch_version("beta"))
    print("dev", get_branch_version("dev"))
