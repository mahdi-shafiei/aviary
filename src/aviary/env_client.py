import logging
from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar, cast

import httpx
from pydantic import BaseModel, Field

from aviary.env import Environment, TaskDataset
from aviary.message import Message
from aviary.tools import MessagesAdapter, Tool, ToolRequestMessage, ToolsAdapter

logger = logging.getLogger(__name__)

# Not sure why, but mypy complains if we use the TEnvState in aviary.env, so redefine here
TEnvState = TypeVar("TEnvState")
TClient = TypeVar("TClient", httpx.Client, httpx.AsyncClient)


class EnvironmentClient(Environment[TEnvState], ABC, Generic[TEnvState]):
    def __init__(
        self,
        reset_endpoint_url: str,
        step_endpoint_url: str,
        request_params: httpx._types.QueryParamTypes | None = None,
        request_headers: httpx._types.HeaderTypes | None = None,
        request_timeout: float | None = None,
        api_key: str | None = None,
    ):
        self._reset_request_url = reset_endpoint_url
        self._step_request_url = step_endpoint_url
        self._request_params = request_params
        self._request_headers = request_headers
        self._request_timeout = request_timeout
        self._api_key = api_key

    async def _post(self, url: str, json: dict[str, Any]) -> httpx.Response:
        async with httpx.AsyncClient() as client:
            headers = httpx.Headers(self._request_headers)
            if self._api_key:
                headers["X-API-Key"] = self._api_key
            response = await client.post(
                url,
                json=json,
                params=self._request_params,
                headers=headers,
                timeout=self._request_timeout,
            )
            response.raise_for_status()
            return response

    async def reset(self) -> tuple[list[Message], list[Tool]]:
        response = await self._post(
            self._reset_request_url, json=self._make_post_json(self.state)
        )
        msgs, tools = response.json()
        return (
            MessagesAdapter.validate_python(msgs),
            ToolsAdapter.validate_python(tools),
        )

    async def step(
        self, action: ToolRequestMessage
    ) -> tuple[list[Message], float, bool, bool]:
        response = await self._post(
            self._step_request_url,
            json=self._make_post_json(self.state) | {"action": action.model_dump()},
        )
        messages, reward, done, truncated = response.json()
        return MessagesAdapter.validate_python(messages), reward, done, truncated

    @abstractmethod
    def _make_post_json(self, state: TEnvState) -> dict[str, Any]:
        """Extract values from state to sent as JSON for all reset/step POSTs."""


class TaskEnvClientState(BaseModel):
    env_id: str = Field(
        description="The ID of the environment (provided by server on start)."
    )


class TaskEnvironmentClient(EnvironmentClient[TaskEnvClientState]):
    """An environment client for environments created by a TaskDatasetServer."""

    def __init__(self, idx: int | None, base_url: str, **kwargs):
        self._idx = idx
        self._start_request_url = base_url + "/start"
        self._close_request_url = base_url + "/close"

        kwargs = {
            "reset_endpoint_url": base_url + "/reset",
            "step_endpoint_url": base_url + "/step",
        } | kwargs

        super().__init__(**kwargs)

    async def _start_remote_env(self) -> str:
        response = await self._post(
            self._start_request_url, json={"task_idx": self._idx}
        )
        return response.json()["env_id"]

    async def reset(self) -> tuple[list[Message], list[Tool]]:
        # defer starting to reset so we can make it async and set the state
        env_id = await self._start_remote_env()
        self.state = TaskEnvClientState(env_id=env_id)

        return await super().reset()

    async def close(self) -> None:
        if not hasattr(self, "state"):
            logger.warning("Attempting to close an environment that was never started.")
            return None

        response = await self._post(
            self._close_request_url, json=self._make_post_json(self.state)
        )
        return response.json()  # noqa: FURB184

    def _make_post_json(self, state: TaskEnvClientState) -> dict[str, Any]:
        return {"env_id": state.env_id}


class TaskDatasetClient(TaskDataset[TaskEnvironmentClient]):
    def __init__(
        self,
        server_url: str,
        # Note that None means no timeout, which is not a good default
        request_timeout: float | None = 300.0,
        api_key: str | None = None,
    ):
        self.server_url = server_url
        self.request_timeout = request_timeout
        self.api_key = api_key

        with self._get_http_client(httpx.Client) as http_client:
            response = http_client.get("/info")
            response.raise_for_status()
            self._len = cast(int | None, response.json()["dataset_size"])

    def _get_http_client(
        self,
        client_class: type[TClient] = httpx.AsyncClient,  # type: ignore[assignment]
    ) -> TClient:
        headers = {}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return client_class(
            base_url=self.server_url, timeout=self.request_timeout, headers=headers
        )

    def get_new_env_by_idx(self, idx: int) -> TaskEnvironmentClient:
        return self._make_env_client(idx)

    def get_new_env(self) -> TaskEnvironmentClient:
        return self._make_env_client(None)

    def _make_env_client(self, idx: int | None) -> TaskEnvironmentClient:
        return TaskEnvironmentClient(
            idx=idx,
            base_url=self.server_url,
            request_timeout=self.request_timeout,
            api_key=self.api_key,
        )

    def __len__(self) -> int:
        if self._len is None:
            raise TypeError("Server did not define dataset length.")

        return self._len
