"""release-gate: 문항·모델 조합을 판정해 릴리스로 출고하고 활성 포인터를 관리한다."""
__version__ = "0.7.0"
SUPPORTED_PIPELINE_MAJOR = 2
from boundary_contracts import PIPELINE_SCHEMA_VERSION  # noqa: E402  (레코드를 만들 때 쓰는 경계 계약 버전)

from .checks import CheckRegistry  # noqa: E402
from .config import GateConfig  # noqa: E402
from .gate import GateEngine, GateServiceImpl  # noqa: E402
from .policy import GatePolicy  # noqa: E402
from .reader import ReleaseReaderImpl  # noqa: E402
from .registry import Registry, memory_registry, open_registry, open_release_reader, postgres_registry, sqlite_registry  # noqa: E402
from .schemas import SchemaSet, SurveySchema  # noqa: E402
from .store import MemoryStore, SqliteStore  # noqa: E402
from .collector import CandidateCollector  # noqa: E402

try:
    from .postgres_store import PostgresStore  # noqa: E402,F401
except ImportError:  # pragma: no cover
    pass
