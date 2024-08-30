from aviary.env import Environment, TaskDataset
from aviary.hotpotqa import HotPotQAEnv


def test_env_construction() -> None:
    hotpotqa_env: HotPotQAEnv = Environment.from_name(
        "hotpotqa",
        question="What is the formula for the volume of Abraham Lincoln's favorite hat?",
        correct_answer="pi*r^2*h",
    )
    assert isinstance(hotpotqa_env, HotPotQAEnv)


def test_dataset_from_name() -> None:
    dataset = TaskDataset.from_name("hotpotqa", split="dev")
    assert isinstance(dataset.get_new_env_by_idx(0), HotPotQAEnv)

    # double-check we can load by difficulty level
    dataset = TaskDataset.from_name(
        "hotpotqa", split="train", difficulty_level={"easy", "hard"}
    )
    assert len(dataset) == 33633