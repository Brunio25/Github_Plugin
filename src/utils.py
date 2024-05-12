from typing import List, Callable

from src.github import PullRequest


def pr_is_approved(pr: PullRequest, user: str) -> bool:
    return len(pr.approves) >= 2 or user in pr.approves


def in_place_filter(array: List, predicate: Callable[[PullRequest], bool]) -> List[PullRequest]:
    removed_elements = [x for x in array if not predicate(x)]
    array[:] = filter(predicate, array)
    return removed_elements
