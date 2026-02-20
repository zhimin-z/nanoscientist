"""
Unified configuration loader.

Priority: CLI arguments > Environment variables (.env) > config.yaml

Sensitive information (API keys) must be configured in .env, not in yaml.
config.yaml is REQUIRED - no default values are used.
"""
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

# ===== Project paths =====
PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# ===== Required keys in config.yaml =====
REQUIRED_KEYS = [
    # Skill Configuration
    "skill_group",
    "max_skills",
    "prune_enabled",
    # Server Configuration
    "port",
    # Tree Building Configuration
    "branching_factor",
    "tree_build_max_workers",
    "tree_build_caching",
    "tree_build_num_retries",
    "tree_build_timeout",
    "max_depth",
    # Search Configuration
    "search_max_parallel",
    "search_temperature",
    "search_timeout",
    "search_caching",
    # Orchestrator Configuration
    "node_timeout",
    "max_concurrent",
    # Retry Configuration
    "retry_base_delay",
    "max_retries",
]


class Config:
    """
    Unified configuration with priority: CLI > ENV > yaml (required).

    Usage:
        # Create config with CLI overrides
        cfg = Config(cli_args={"port": 8080})

        # Access values
        cfg.port  # 8080 (from CLI)
        cfg.skill_group  # from yaml
        cfg.llm_model  # from .env

        # Use custom config file
        cfg = Config(config_path="path/to/custom.yaml")
    """

    _yaml_cache = None
    _instance = None
    _config_path = None  # Custom config file path

    def __init__(self, cli_args: dict = None, config_path: str = None):
        self._cli = cli_args or {}
        if config_path:
            Config._config_path = Path(config_path)
        if Config._yaml_cache is None:
            Config._yaml_cache = self._load_yaml()

    @classmethod
    def get_instance(cls, cli_args: dict = None, config_path: str = None) -> "Config":
        """Get singleton instance, optionally updating CLI args."""
        if cls._instance is None:
            cls._instance = cls(cli_args, config_path=config_path)
        elif cli_args:
            cls._instance._cli.update(cli_args)
        return cls._instance

    @classmethod
    def reset(cls):
        """Reset singleton (for testing)."""
        cls._instance = None
        cls._yaml_cache = None
        cls._config_path = None

    def _load_yaml(self) -> dict:
        """Load config.yaml with validation."""
        # Use custom path if specified, otherwise default
        path = Config._config_path or (PROJECT_ROOT / "config" / "config.yaml")
        if not path.exists():
            raise FileNotFoundError(
                f"Configuration file not found: {path}\n"
                "Please copy config/config.yaml.example to config/config.yaml and modify as needed."
            )

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        # Check required keys
        missing = [k for k in REQUIRED_KEYS if k not in data]
        if missing:
            raise ValueError(f"config.yaml missing required keys: {missing}")

        return data

    def get(self, key: str, env_key: str = None):
        """
        Get config value with priority: CLI > ENV > yaml (required).

        Args:
            key: Config key (used for CLI and yaml lookup)
            env_key: Environment variable name (optional)

        Raises:
            KeyError: If key not found in any source
        """
        # 1. CLI args (highest priority)
        if key in self._cli and self._cli[key] is not None:
            return self._cli[key]

        # 2. Environment variable
        if env_key and os.getenv(env_key):
            return os.getenv(env_key)

        # 3. YAML config (required)
        if Config._yaml_cache and key in Config._yaml_cache:
            return Config._yaml_cache[key]

        # No default - raise error
        raise KeyError(f"config.yaml missing required key: {key}")

    # ===== Skill Configuration =====
    @property
    def skill_group(self) -> str:
        return self.get("skill_group")

    @property
    def max_skills(self) -> int:
        return int(self.get("max_skills"))

    @property
    def prune_enabled(self) -> bool:
        val = self.get("prune_enabled", "PRUNE_ENABLED")
        if isinstance(val, bool):
            return val
        return str(val).lower() == "true"

    # ===== Server Configuration =====
    @property
    def port(self) -> int:
        return int(self.get("port"))

    # ===== LLM Configuration (from .env only) =====
    @property
    def llm_model(self) -> str:
        return os.getenv("LLM_MODEL", "openai/gpt-4o-mini")

    @property
    def llm_base_url(self) -> str:
        return os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")

    @property
    def llm_api_key(self) -> str:
        return os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")

    @property
    def llm_max_retries(self) -> int:
        return int(os.getenv("LLM_MAX_RETRIES", "3"))

    # ===== Embedding Configuration (from .env only) =====
    @property
    def embedding_model(self) -> str:
        return os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

    @property
    def embedding_base_url(self) -> str:
        return os.getenv("EMBEDDING_BASE_URL") or os.getenv("OPENAI_BASE_URL")

    @property
    def embedding_api_key(self) -> str:
        return os.getenv("EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY")

    @property
    def embedding_batch_size(self) -> int:
        return int(os.getenv("EMBEDDING_BATCH_SIZE", "100"))

    # ===== Tree Configuration =====
    @property
    def branching_factor(self) -> int:
        return int(self.get("branching_factor"))

    @property
    def tree_build_max_workers(self) -> int:
        return int(self.get("tree_build_max_workers"))

    @property
    def tree_build_caching(self) -> bool:
        val = self.get("tree_build_caching")
        if isinstance(val, bool):
            return val
        return str(val).lower() == "true"

    @property
    def tree_build_num_retries(self) -> int:
        return int(self.get("tree_build_num_retries"))

    @property
    def tree_build_timeout(self) -> float:
        return float(self.get("tree_build_timeout"))

    @property
    def max_depth(self) -> int:
        return int(self.get("max_depth"))

    # ===== Search Configuration =====
    @property
    def search_max_parallel(self) -> int:
        return int(self.get("search_max_parallel"))

    @property
    def search_temperature(self) -> float:
        return float(self.get("search_temperature"))

    @property
    def search_timeout(self) -> float:
        return float(self.get("search_timeout"))

    @property
    def search_caching(self) -> bool:
        val = self.get("search_caching")
        if isinstance(val, bool):
            return val
        return str(val).lower() == "true"

    # ===== Orchestrator Configuration =====
    @property
    def node_timeout(self) -> float:
        return float(self.get("node_timeout"))

    @property
    def max_concurrent(self) -> int:
        return int(self.get("max_concurrent"))

    # ===== Retry Configuration =====
    @property
    def retry_base_delay(self) -> float:
        return float(self.get("retry_base_delay"))

    @property
    def max_retries(self) -> int:
        return int(self.get("max_retries"))


# ===== Global config instance =====
def get_config(cli_args: dict = None, config_path: str = None) -> Config:
    """Get global config instance."""
    return Config.get_instance(cli_args, config_path=config_path)


# ===== Backward compatibility: module-level variables =====
# These are kept for backward compatibility with existing code
# Path variables are always available
DATA_DIR = PROJECT_ROOT / "data"
SKILLS_DIR = DATA_DIR / "skill_seeds"

# LLM Configuration (from .env, with sensible defaults for optional items)
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")
LLM_BASE_URL = os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")
LLM_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))

# Embedding Configuration (from .env, with sensible defaults for optional items)
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL") or os.getenv("OPENAI_BASE_URL")
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY")
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "100"))


# ===== Lazy-loaded config values =====
# These require config.yaml to exist and will raise errors if not configured
# Uses __getattr__ for backward-compatible module-level access

_LAZY_CONFIG_KEYS = {
    # Tree Building
    "BRANCHING_FACTOR": "branching_factor",
    "TREE_BUILD_MAX_WORKERS": "tree_build_max_workers",
    "TREE_BUILD_CACHING": "tree_build_caching",
    "TREE_BUILD_NUM_RETRIES": "tree_build_num_retries",
    "TREE_BUILD_TIMEOUT": "tree_build_timeout",
    "MAX_DEPTH": "max_depth",
    # Search
    "SEARCH_MAX_PARALLEL": "search_max_parallel",
    "SEARCH_TEMPERATURE": "search_temperature",
    "SEARCH_TIMEOUT": "search_timeout",
    "SEARCH_CACHING": "search_caching",
    # Orchestrator
    "NODE_TIMEOUT": "node_timeout",
    "MAX_CONCURRENT": "max_concurrent",
    # Retry
    "RETRY_BASE_DELAY": "retry_base_delay",
    "MAX_RETRIES": "max_retries",
    # Others
    "PRUNE_ENABLED": "prune_enabled",
}


_BOOL_KEYS = {"PRUNE_ENABLED", "TREE_BUILD_CACHING", "SEARCH_CACHING"}
_FLOAT_KEYS = {"TREE_BUILD_TIMEOUT", "SEARCH_TEMPERATURE", "SEARCH_TIMEOUT", "NODE_TIMEOUT", "RETRY_BASE_DELAY"}


def __getattr__(name: str):
    """Lazy load config values from config.yaml on first access."""
    if name in _LAZY_CONFIG_KEYS:
        yaml_key = _LAZY_CONFIG_KEYS[name]
        value = get_config().get(yaml_key)
        if name in _BOOL_KEYS:
            if isinstance(value, bool):
                return value
            return str(value).lower() == "true"
        if name in _FLOAT_KEYS:
            return float(value)
        return int(value)
    raise AttributeError(f"module 'config' has no attribute '{name}'")

# ===== Skill Groups Configuration =====
# Alias mapping for backward compatibility (e.g., "default" -> "skill_seeds")
SKILL_GROUP_ALIASES = {"default": "skill_seeds"}

SKILL_GROUPS = [
    {
        "id": "skill_seeds",
        "name": "Curated (RECOMMENDED)",
        "description": "Carefully curated skill set (~50 skills)",
        "skills_dir": str(DATA_DIR / "skill_seeds"),
        "tree_path": str(Path(__file__).parent / "skill_retriever/capability_tree/tree.yaml"),
        "is_default": True,
    },
    {
        "id": "top500",
        "name": "Top 500",
        "description": "Top 500 skills collection from skills.sh",
        "skills_dir": str(DATA_DIR / "skill_top500"),
        "tree_path": str(Path(__file__).parent / "skill_retriever/capability_tree/tree_top500.yaml"),
    },
    {
        "id": "top1000",
        "name": "Top 1000",
        "description": "Top 1000 skills collection from skills.sh",
        "skills_dir": str(DATA_DIR / "skill_top1000"),
        "tree_path": str(Path(__file__).parent / "skill_retriever/capability_tree/tree_top1000.yaml"),
    },
]


DEMO_TASKS = [
    {
        "id": "frontend_debug",
        "title": "Frontend Debug Report",
        "description": "Fix login page bug and generate report",
        "prompt": "I am a front-end developer. Users have reported that a bug occurs when accessing the login page I wrote on a mobile phone. The code for my login page is login.html. Please help me identify and fix the bug, and write a bug fix report. The report should include a screenshot of the problematic web page before the bug fix and a screenshot of the normal web page after the bug fix. In the screenshots, the location of the bug should be highlighted with clear and eye-catching markers. The report should be saved as bug_report.md.",
        "files": ["artifacts/login.html"],
        "icon": "bug",
    },
    {
        "id": "ui_research",
        "title": "Fusion UI Design",
        "description": "Visual design research for knowledge management product",
        "prompt": "I am a product designer, and our company is planning to build a knowledge management software product. Therefore, I need you to research multiple related products, such as Notion and Confluence, and produce a visual design style research report about them. The visual style research report should be saved as report.docx and must include screenshots of these software products. Then, based on the analysis, synthesize the design characteristics of these products and generate three design concept images for a knowledge management software to provide design inspiration. The design concept images should be saved as fusion_design_1.png, fusion_design_2.png, and fusion_design_3.png, respectively.",
        "files": [],
        "icon": "design",
    },
    {
        "id": "paper_promotion",
        "title": "Paper Promotion Assistant",
        "description": "Multi-platform promotion plan for research paper",
        "prompt": "As a PhD student, I have recently completed a research paper and would like to effectively promote it on both domestic and international social media platforms. In addition, I need help creating online materials that can serve as a central hub for clearly presenting and disseminating my research findings to a broader audience. My paper is located locally at Avengers.pdf.",
        "files": ["artifacts/Avengers.pdf"],
        "icon": "paper",
    },
    {
        "id": "cat_meme_video",
        "title": "Cat Meme Video Generation",
        "description": "Generate cat meme video of boss questioning employee",
        "prompt": "I am a short-video content creator, and I need you to generate a funny cat meme video. The theme of the video is a boss questioning an employee about work progress, and the employee gives a clever response. The questioning cat represents the boss, and the aggrieved cat represents the employee.\nVideo materials: The video materials for the questioning cat and the aggrieved cat are in video.mp4. The background image for the video is background.jpg.\nVideo quality requirements: Completely remove the green screen background from both the questioning cat and the aggrieved cat video materials, and replace it with background.jpg. Keep the background image’s aspect ratio without distortion. The cats should occupy the main focus of the frame, and the integrity of the cat footage should be preserved as much as possible.\nFormat requirements: The generated video must have the same duration as the original video material. All text must be in Chinese, so please pay attention to potential text encoding issues.\nText requirements: Identity labels reading “Boss” and “Employee” should appear next to the questioning cat and the aggrieved cat respectively. The timing and duration of the dialogue subtitles must be accurate. Continuous meowing by a single cat should be treated as one sentence. The dialogue between the two cats should be humorous, and the employee’s responses to the boss should be witty and have strong potential for widespread sharing on the internet. The length of the dialogue text should be determined based on the duration of each line spoken by the two cats.",
        "files": ["artifacts/video.mp4", "artifacts/bg.jpg"],
        "icon": "video",
    },
]