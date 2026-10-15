"""release-gate: 문항·모델 조합을 판정해 릴리스로 출고하고 활성 포인터를 관리한다."""
__version__ = "0.3.0"
SUPPORTED_PIPELINE_MAJOR = 1
PIPELINE_SCHEMA_VERSION = "1.0"

from .checks import CheckRegistry  # noqa: E402
from .config import GateConfig  # noqa: E402
from .gate import GateEngine, GateServiceImpl  # noqa: E402
from .policy import GatePolicy  # noqa: E402
from .reader import ReleaseReaderImpl  # noqa: E402
from .registry import Registry, memory_registry, open_release_reader, sqlite_registry  # noqa: E402
from .schemas import SchemaSet, SurveySchema  # noqa: E402
from .store import MemoryStore, SqliteStore  # noqa: E402
from .worker import AcceptanceRunner, EvalJob, EvalWorker  # noqa: E402
from .collector import CandidateCollector  # noqa: E402
