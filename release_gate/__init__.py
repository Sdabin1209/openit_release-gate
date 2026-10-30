"""release-gate: 문항·모델 조합을 판정해 릴리스로 출고하고 활성 포인터를 관리한다.

구조(docs/components.md): components/(업무와 통신, 서로를 모른다), wiring/(연결·주입), types/(schemas/에서 생성), 진입점 main.py.
"""
__version__ = "0.7.0"
from .components.check_contract_compat import SUPPORTED_PIPELINE_MAJOR  # noqa: E402,F401  (게이트가 읽을 수 있는 경계 계약 MAJOR)
from boundary_contracts import PIPELINE_SCHEMA_VERSION  # noqa: E402  (레코드를 만들 때 쓰는 경계 계약 버전)

from .components.gate_policy import GatePolicy  # noqa: E402
from .components.registry_store import MemoryStore, SqliteStore  # noqa: E402
from .components.release_reader import ReleaseReaderImpl  # noqa: E402
from .config import GateConfig  # noqa: E402
from .wiring.check_registry import CheckRegistry  # noqa: E402
from .wiring.collector import CandidateCollector  # noqa: E402
from .wiring.engine import GateEngine  # noqa: E402
from .wiring.registry import Registry, memory_registry, open_registry, open_release_reader, postgres_registry, sqlite_registry  # noqa: E402
from .wiring.schema_set import SchemaSet  # noqa: E402
from .wiring.service import GateServiceImpl  # noqa: E402
from .wiring.survey import SurveySchema  # noqa: E402

try:
    from .components.registry_store import PostgresStore  # noqa: E402,F401
except ImportError:  # pragma: no cover
    pass
