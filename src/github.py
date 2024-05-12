from dataclasses import dataclass
from datetime import datetime
from multiprocessing import Manager, Process
from typing import List, Optional

import requests


@dataclass
class PullRequest:
    repo: str
    title: str
    url: str
    is_draft: bool
    created_by: str
    created_at: datetime
    approves: List[str]


class GithubError(Exception):
    def __init__(self, title: str, description: str):
        super().__init__()
        self.title = title
        self.description = description


class Github:
    __GITHUB_URL: str = "https://{hostname}/api/v3/orgs/{org}/repos"
    __DATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

    def __init__(self, hostname: str, org: str, access_token: str):
        self.__GITHUB_URL = self.__GITHUB_URL.format(hostname=hostname, org=org)
        self.__headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {access_token}",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        self.repos: Optional[List[PullRequest]] = None

    def get_prs(self) -> List[PullRequest]:
        try:
            if self.repos is None:
                self.repos = requests.get(self.__GITHUB_URL, headers=self.__headers).json()

            result_matrix = Manager().dict()
            processes = list(
                map(lambda r: Process(target=self.__fetch_prs, args=(result_matrix, r["url"])), self.repos))

            for pr in processes:
                pr.start()
            for pr in processes:
                pr.join()

            result: [PullRequest] = []
            for prs in result_matrix.values():
                for pr in prs:
                    result.append(pr)

            return result
        except Exception as _:
            raise GithubError("Error getting Pull Requests", "Check you connectivity, github url and access token")

    def __fetch_prs(self, shared_result: dict, repo_url: str):
        prs = requests.get(f"{repo_url}/pulls", headers=self.__headers)

        result_matrix = Manager().dict()
        processes = list(map(lambda pr: Process(target=self.__build_pr, args=(result_matrix, pr)), prs.json()))
        for pr in processes:
            pr.start()
        for pr in processes:
            pr.join()

        result = []
        for pr in result_matrix.values():
            result.append(pr)

        shared_result[repo_url] = result

    def __build_pr(self, shared_result: dict, pr: dict):
        shared_result[pr["url"]] = PullRequest(
            repo=pr["head"]["repo"]["name"],
            title=pr["title"],
            url=pr["html_url"],
            is_draft=pr["draft"],
            created_by=pr["user"]["login"],
            created_at=datetime.strptime(pr["created_at"], self.__DATE_FORMAT),
            approves=self.__get_pr_approves(pr["url"])
        )

    def __get_pr_approves(self, pr_url: str):
        reviews = requests.get(f"{pr_url}/reviews", headers=self.__headers)
        approves = filter(lambda r: r["state"] == "APPROVED", reviews.json())
        return list(map(lambda r: r["user"]["login"], approves))
