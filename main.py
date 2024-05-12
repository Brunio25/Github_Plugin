import re
from datetime import datetime, timedelta
from enum import Enum, auto
from pathlib import Path
from typing import List, Optional, Tuple

from ulauncher.api.client.EventListener import EventListener
from ulauncher.api.client.Extension import Extension
from ulauncher.api.shared.action.ActionList import ActionList
from ulauncher.api.shared.action.DoNothingAction import DoNothingAction
from ulauncher.api.shared.action.ExtensionCustomAction import ExtensionCustomAction
from ulauncher.api.shared.action.OpenUrlAction import OpenUrlAction
from ulauncher.api.shared.action.RenderResultListAction import RenderResultListAction
from ulauncher.api.shared.event import KeywordQueryEvent, ItemEnterEvent, PreferencesEvent, PreferencesUpdateEvent
from ulauncher.api.shared.item.ExtensionResultItem import ExtensionResultItem

from src.github import Github, PullRequest, GithubError
from src.utils import pr_is_approved, in_place_filter

IMAGES_FOLDER = Path(__file__).parent / 'images'
GITHUB_LOGO = (IMAGES_FOLDER / 'github_logo.png').__str__()
GITHUB_APPROVED_LOGO = (IMAGES_FOLDER / 'github_approved_logo.png').__str__()
GITHUB_USER_LOGO = (IMAGES_FOLDER / 'github_user_logo.png').__str__()
GITHUB_USER_APPROVED_LOGO = (IMAGES_FOLDER / 'github_user_approved_logo.png').__str__()
ERROR_ICON = (IMAGES_FOLDER / 'github_error_logo.png').__str__()


class GithubExtension(Extension):
    github_controller = None

    def __init__(self):
        super().__init__()
        self.last_request = None
        self.multiselect_urls: List[str] = []

        self.subscribe(PreferencesEvent, PreferencesEventListener())
        self.subscribe(ItemEnterEvent, MultiselectEventListener())
        self.subscribe(ItemEnterEvent, ApprovedPrsEventListener())
        self.subscribe(PreferencesUpdateEvent, PreferencesUpdateEventListener())


class KeywordQueryEventListener(EventListener):
    def on_event(self, event: KeywordQueryEvent, extension: GithubExtension):
        extension.multiselect_urls = []
        query = event.get_query().get_argument(default="")

        return RenderResultListAction(extension.github_controller
                                      .build_pr_items(PrType.OPEN,
                                                      lambda pr:
                                                      re.search(query, pr.title, re.IGNORECASE) or
                                                      re.search(query, pr.repo, re.IGNORECASE),
                                                      include_approved_button=True))


class PrType(Enum):
    OPEN = auto()
    APPROVED = auto()


class CustomActionEvent(Enum):
    MULTISELECT = auto()
    APPROVED_PRS = auto()

    @classmethod
    def multiselect(cls, multiselect_value: PrType):
        enum_constant = cls[CustomActionEvent.MULTISELECT.name]
        if not enum_constant:
            return None

        enum_constant.multiselect_value = multiselect_value
        return enum_constant


class MultiselectEventListener(EventListener):
    def on_event(self, event: ItemEnterEvent, extension: GithubExtension):
        data = event.get_data()
        if data["event"] is not CustomActionEvent.MULTISELECT:
            return

        extension.multiselect_urls.append(data["pr_url"])

        items = [
            ExtensionResultItem(
                name=f"Open {len(extension.multiselect_urls)} Pull Requests",
                description=f"⚠️ Do not write a query if you want to open multiple Pull Requests! ⚠️",
                icon=GITHUB_APPROVED_LOGO,
                on_enter=ActionList([OpenUrlAction(url) for url in extension.multiselect_urls])
            )
        ]

        items.extend(extension.github_controller.build_pr_items(data["event"].multiselect_value,
                                                                lambda pr: not any(pr.url == url
                                                                                   for url in
                                                                                   extension.multiselect_urls)))

        return RenderResultListAction(items)


class ApprovedPrsEventListener(EventListener):
    def on_event(self, event: ItemEnterEvent, extension: GithubExtension):
        data = event.get_data()

        if data["event"] is not CustomActionEvent.APPROVED_PRS:
            return

        return RenderResultListAction(
            extension.github_controller.build_pr_items(PrType.APPROVED)
        )


class PreferencesEventListener(EventListener):
    def on_event(self, event: PreferencesEvent, extension: GithubExtension):
        preferences = event.preferences
        extension.github_controller = GithubController(hostname=preferences["hostname"],
                                                       org=preferences["org"],
                                                       access_token=preferences["access_token"],
                                                       user=preferences["user"])
        extension.subscribe(KeywordQueryEvent, KeywordQueryEventListener())


class PreferencesUpdateEventListener(EventListener):
    def on_event(self, event: PreferencesUpdateEvent, extension: GithubExtension):
        preferences = extension.preferences.copy()
        preferences.update({event.id: event.new_value})
        extension.github_controller = GithubController(hostname=preferences["hostname"],
                                                       org=preferences["org"],
                                                       access_token=preferences["access_token"],
                                                       user=preferences["user"])


class GithubController:
    def __init__(self, hostname: str, org: str, access_token: str, user: str):
        self.user = user
        self.last_request: Optional[Tuple[datetime, Tuple[List[PullRequest], List[PullRequest]]]] = None
        self.github_client = Github(hostname=hostname, org=org, access_token=access_token)

    def build_pr_items(self, pr_type: PrType, predicate=None, include_approved_button: bool = False) -> \
            List[ExtensionResultItem]:
        try:
            open_prs, approved_prs = self.__get_prs()
        except GithubError as e:
            return [ExtensionResultItem(
                name=e.title,
                description=e.description,
                icon=ERROR_ICON,
                on_enter=DoNothingAction()
            )]

        relevant_prs = approved_prs if pr_type is PrType.APPROVED else open_prs
        icon = GITHUB_APPROVED_LOGO if pr_type is PrType.APPROVED else GITHUB_LOGO
        user_icon = GITHUB_USER_APPROVED_LOGO if pr_type is PrType.APPROVED else GITHUB_USER_LOGO

        pr_type = CustomActionEvent.multiselect(pr_type)

        items = [
            ExtensionResultItem(
                name=pr.title,
                description=f"{pr.repo}\n{pr.url}",
                icon=icon if pr.created_by != self.user else user_icon,
                on_enter=OpenUrlAction(pr.url),
                on_alt_enter=ExtensionCustomAction({"event": pr_type, "pr_url": pr.url}, keep_app_open=True)
            ) for pr in relevant_prs if (True if predicate is None else predicate(pr))
        ]

        if include_approved_button and len(approved_prs) != 0:
            items.append(self.__build_approved_button(approved_prs))

        return items

    @staticmethod
    def __build_approved_button(approved_prs: List[PullRequest]) -> ExtensionResultItem:
        return ExtensionResultItem(
            name="Approved Pull Requests",
            description=f"View {len(approved_prs)} approved Pull Requests",
            icon=GITHUB_APPROVED_LOGO,
            on_enter=ExtensionCustomAction({"event": CustomActionEvent.APPROVED_PRS},
                                           keep_app_open=True)
        )

    def __get_prs(self) -> Tuple[List[PullRequest], List[PullRequest]]:
        return self.last_request[1] \
            if self.last_request and datetime.now() - self.last_request[0] < timedelta(minutes=1) \
            else self.__fetch_prs()

    def __fetch_prs(self) -> Tuple[List[PullRequest], List[PullRequest]]:
        prs = self.github_client.get_prs()
        prs_tuple = self.__order_filter_prs(prs)
        self.last_request = (datetime.now(), prs_tuple)
        return prs_tuple

    def __order_filter_prs(self, prs: List[PullRequest]) -> Tuple[List[PullRequest], List[PullRequest]]:
        prs[:] = filter(lambda pr: not pr.is_draft, prs)
        prs.sort(key=lambda pr: pr.created_at, reverse=True)
        prs.sort(key=lambda pr: -1 if pr.created_by == self.user else 0)
        approved_prs = in_place_filter(prs, lambda pr: not pr_is_approved(pr, self.user))
        return prs, approved_prs


if __name__ == '__main__':
    GithubExtension().run()
