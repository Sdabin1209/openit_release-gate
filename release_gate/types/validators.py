# 생성 파일: scripts/gen_types.py가 schemas/*.json에서 만든다. 손으로 고치지 않는다.
"""영역 내부 스키마 묶음과 검증 함수. 컴포넌트는 입력을 받으면 validate(타입 이름, 레코드)로 검사한다.
validate_contract는 boundary-contracts 패키지의 계약 스키마로 검사한다(복사본을 두지 않는다)."""
from __future__ import annotations

import json
from typing import Optional

import boundary_contracts
import jsonschema
from boundary_contracts.errors import BoundaryError
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

_INTERNAL = json.loads(r'''{
 "AdapterInfo": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "additionalProperties": false,
  "description": "릴리스에 묶는 어댑터 정보(계약 ReleaseManifest.adapters의 값)",
  "properties": {
   "adapter_hash": {
    "minLength": 1,
    "type": "string"
   },
   "serving_config": {
    "type": "object"
   },
   "serving_config_hash": {
    "minLength": 1,
    "type": "string"
   },
   "system_prompt": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties": false,
    "description": "",
    "properties": {
     "path": {
      "minLength": 1,
      "type": "string"
     },
     "sha256": {
      "minLength": 1,
      "type": "string"
     }
    },
    "required": [
     "path",
     "sha256"
    ],
    "title": "PromptRef",
    "type": "object"
   }
  },
  "required": [
   "adapter_hash",
   "serving_config",
   "serving_config_hash",
   "system_prompt"
  ],
  "title": "AdapterInfo",
  "type": "object"
 },
 "AdapterInspectInput": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "additionalProperties": false,
  "description": "어댑터 점검 입력: 역할, 받아 둔 디렉터리 경로, 그 역할의 매니페스트",
  "properties": {
   "manifest": {
    "properties": {
     "adapter_hash": {
      "type": "string"
     },
     "serving_config": {
      "type": "object"
     },
     "serving_config_hash": {
      "type": "string"
     },
     "system_prompt": {
      "required": [
       "path",
       "sha256"
      ],
      "type": "object"
     }
    },
    "required": [
     "adapter_hash",
     "serving_config",
     "serving_config_hash",
     "system_prompt"
    ],
    "type": "object"
   },
   "path": {
    "minLength": 1,
    "type": "string"
   },
   "role": {
    "enum": [
     "LORA_A",
     "LORA_B"
    ]
   }
  },
  "required": [
   "role",
   "path",
   "manifest"
  ],
  "title": "AdapterInspectInput",
  "type": "object"
 },
 "ApprovalDraftInput": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "additionalProperties": false,
  "description": "승인 기록 만들기 입력: 사람의 결정 본문, 토큰 역할, 인증된 승인자 ID, 시각, (대조를 마친) 현재 보고서 해시",
  "properties": {
   "approver_id": {
    "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
    "type": "string"
   },
   "body": {
    "type": "object"
   },
   "current_report_hash": {
    "pattern": "^sha256:[0-9a-f]{64}$",
    "type": "string"
   },
   "decided_at": {
    "minLength": 1,
    "type": "string"
   },
   "submission_id": {
    "description": "본문의 submission_id",
    "pattern": "^sub-.",
    "type": "string"
   },
   "token_role": {
    "minLength": 1,
    "type": "string"
   }
  },
  "required": [
   "body",
   "submission_id",
   "token_role",
   "approver_id",
   "decided_at"
  ],
  "title": "ApprovalDraftInput",
  "type": "object"
 },
 "AutoVerdict": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "additionalProperties": false,
  "description": "자동 검사 종합 출력: 판정 기록의 checks 항목과 반려 사유(사유가 있으면 반려 대상)",
  "properties": {
   "reasons": {
    "items": {
     "type": "string"
    },
    "type": "array"
   },
   "records": {
    "items": {
     "$schema": "https://json-schema.org/draft/2020-12/schema",
     "additionalProperties": false,
     "description": "",
     "properties": {
      "detail": {
       "type": "string"
      },
      "evidence_ref": {
       "type": "string"
      },
      "kind": {
       "enum": [
        "CONTRACT_COMPAT",
        "QUESTION_MODEL_COMPAT",
        "ACCEPTANCE_EVAL",
        "CLINICAL_SAFETY"
       ]
      },
      "result": {
       "enum": [
        "PASS",
        "FAIL"
       ]
      }
     },
     "required": [
      "kind",
      "result"
     ],
     "title": "CheckRecordOut",
     "type": "object"
    },
    "type": "array"
   }
  },
  "required": [
   "records",
   "reasons"
  ],
  "title": "AutoVerdict",
  "type": "object"
 },
 "AutoVerdictInput": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "additionalProperties": false,
  "description": "자동 검사 종합 입력: 검사 종류별 결과",
  "properties": {
   "results": {
    "items": {
     "$schema": "https://json-schema.org/draft/2020-12/schema",
     "additionalProperties": false,
     "description": "",
     "properties": {
      "kind": {
       "enum": [
        "CONTRACT_COMPAT",
        "QUESTION_MODEL_COMPAT",
        "ACCEPTANCE_EVAL",
        "CLINICAL_SAFETY"
       ]
      },
      "outcome": {
       "$ref": "CheckOutcome.json"
      }
     },
     "required": [
      "kind",
      "outcome"
     ],
     "title": "KindedOutcome",
     "type": "object"
    },
    "type": "array"
   }
  },
  "required": [
   "results"
  ],
  "title": "AutoVerdictInput",
  "type": "object"
 },
 "CandidateSelectionInput": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "additionalProperties": false,
  "description": "후보 선정 입력: 검수 완료 최신 문항 세트들과 감시 중인 패키지의 모든 버전",
  "properties": {
   "question_sets": {
    "items": {
     "description": "계약 ArtifactRef(id, version, content_hash). 모양은 계약이 정본이고 경계에서 계약으로 검증된 값이다",
     "required": [
      "id",
      "version",
      "content_hash"
     ],
     "type": "object"
    },
    "type": "array"
   },
   "versions": {
    "items": {
     "$ref": "ModelVersionSummary.json"
    },
    "type": "array"
   }
  },
  "required": [
   "question_sets",
   "versions"
  ],
  "title": "CandidateSelectionInput",
  "type": "object"
 },
 "CandidateSubjects": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "additionalProperties": false,
  "description": "후보 선정 출력: 제출할 조합 목록",
  "properties": {
   "subjects": {
    "items": {
     "description": "판정 대상 조합(계약 GateSubmission.subject). 세 참조의 모양은 계약이 정본이다",
     "required": [
      "question_set",
      "lora_a",
      "lora_b"
     ],
     "type": "object"
    },
    "type": "array"
   }
  },
  "required": [
   "subjects"
  ],
  "title": "CandidateSubjects",
  "type": "object"
 },
 "CheckInput": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "additionalProperties": false,
  "description": "자동 검사 하나의 입력: 판정 대상 내용, 그 검사의 정책 인자, 기준 버전",
  "properties": {
   "data": {
    "$ref": "SubjectData.json"
   },
   "params": {
    "properties": {
     "require_lineage_runtime_match": {
      "type": "boolean"
     }
    },
    "type": "object"
   },
   "versions": {
    "$ref": "CheckVersions.json"
   }
  },
  "required": [
   "data",
   "params",
   "versions"
  ],
  "title": "CheckInput",
  "type": "object"
 },
 "CheckOutcome": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "additionalProperties": false,
  "description": "자동 검사 하나의 결과. 합격 임계치는 어디에도 없다(D53)",
  "properties": {
   "detail": {
    "type": "string"
   },
   "evidence_ref": {
    "type": "string"
   },
   "result": {
    "enum": [
     "PASS",
     "FAIL"
    ]
   }
  },
  "required": [
   "result"
  ],
  "title": "CheckOutcome",
  "type": "object"
 },
 "CheckVersions": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "additionalProperties": false,
  "description": "검사가 비교하는 기준 버전(읽지 못한 값은 null이고 검사가 불일치로 본다)",
  "properties": {
   "action_rules_version": {
    "type": [
     "string",
     "null"
    ]
   },
   "model_runtime_version": {
    "type": [
     "string",
     "null"
    ]
   },
   "supported_runtime_contract_version": {
    "minimum": 1,
    "type": "integer"
   },
   "survey_contract_version": {
    "minimum": 1,
    "type": "integer"
   }
  },
  "required": [
   "supported_runtime_contract_version",
   "action_rules_version",
   "model_runtime_version",
   "survey_contract_version"
  ],
  "title": "CheckVersions",
  "type": "object"
 },
 "DecisionDraft": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "additionalProperties": false,
  "allOf": [
   {
    "if": {
     "properties": {
      "kind": {
       "const": "AUTO_REJECT"
      }
     }
    },
    "then": {
     "not": {
      "required": [
       "approval"
      ]
     },
     "required": [
      "reject_reasons"
     ]
    }
   },
   {
    "if": {
     "properties": {
      "kind": {
       "const": "HUMAN_REJECT"
      }
     }
    },
    "then": {
     "required": [
      "reject_reasons",
      "approval"
     ]
    }
   },
   {
    "if": {
     "properties": {
      "kind": {
       "const": "PASS"
      }
     }
    },
    "then": {
     "not": {
      "required": [
       "reject_reasons"
      ]
     },
     "required": [
      "approval"
     ]
    }
   }
  ],
  "description": "판정 기록 만들기 입력. kind: AUTO_REJECT(자동 검사 반려), HUMAN_REJECT(승인자 반려), PASS(승인자 APPROVE 뒤)",
  "properties": {
   "approval": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties": false,
    "description": "승인 기록 참조",
    "properties": {
     "approval_id": {
      "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
      "type": "string"
     },
     "approver_id": {
      "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
      "type": "string"
     },
     "approver_role": {
      "minLength": 1,
      "type": "string"
     }
    },
    "required": [
     "approval_id",
     "approver_role",
     "approver_id"
    ],
    "title": "ApprovalRefs",
    "type": "object"
   },
   "checks": {
    "items": {
     "type": "object"
    },
    "type": "array"
   },
   "decided_at": {
    "minLength": 1,
    "type": "string"
   },
   "gate_policy_version": {
    "minLength": 1,
    "type": "string"
   },
   "gate_role": {
    "minLength": 1,
    "type": "string"
   },
   "kind": {
    "enum": [
     "AUTO_REJECT",
     "HUMAN_REJECT",
     "PASS"
    ]
   },
   "reject_reasons": {
    "items": {
     "type": "string"
    },
    "minItems": 1,
    "type": "array"
   },
   "runtime_contract_version": {
    "minimum": 1,
    "type": "integer"
   },
   "sequence": {
    "maximum": 9999,
    "minimum": 1,
    "type": "integer"
   },
   "subject": {
    "description": "판정 대상 조합(계약 GateSubmission.subject). 세 참조의 모양은 계약이 정본이다",
    "required": [
     "question_set",
     "lora_a",
     "lora_b"
    ],
    "type": "object"
   },
   "subject_unreadable": {
    "type": "boolean"
   },
   "submission_id": {
    "pattern": "^sub-.",
    "type": "string"
   }
  },
  "required": [
   "kind",
   "submission_id",
   "sequence",
   "decided_at",
   "gate_policy_version",
   "subject",
   "runtime_contract_version",
   "checks",
   "gate_role"
  ],
  "title": "DecisionDraft",
  "type": "object"
 },
 "DecisionRule": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "description": "GateDecision의 PASS/REJECT 규칙(D53). 모양은 계약 GateDecision이 정본이고 여기는 판정 규칙만 적는다: PASS는 모든 검사가 PASS이고 HUMAN_APPROVAL 검사와 release_id·approval_id가 있으며 사유가 없다. REJECT는 사유와 코드가 있고 release_id가 없다",
  "oneOf": [
   {
    "not": {
     "anyOf": [
      {
       "required": [
        "reject_reasons"
       ]
      },
      {
       "required": [
        "reject_codes"
       ]
      }
     ]
    },
    "properties": {
     "checks": {
      "contains": {
       "properties": {
        "kind": {
         "const": "HUMAN_APPROVAL"
        }
       },
       "required": [
        "kind"
       ]
      },
      "items": {
       "properties": {
        "result": {
         "const": "PASS"
        }
       }
      }
     },
     "result": {
      "const": "PASS"
     }
    },
    "required": [
     "release_id",
     "approval_id"
    ]
   },
   {
    "not": {
     "required": [
      "release_id"
     ]
    },
    "properties": {
     "reject_codes": {
      "minItems": 1,
      "type": "array"
     },
     "reject_reasons": {
      "minItems": 1,
      "type": "array"
     },
     "result": {
      "const": "REJECT"
     }
    },
    "required": [
     "reject_reasons",
     "reject_codes"
    ]
   }
  ],
  "properties": {
   "checks": {
    "items": {
     "required": [
      "kind",
      "result"
     ],
     "type": "object"
    },
    "type": "array"
   },
   "result": {
    "enum": [
     "PASS",
     "REJECT"
    ]
   }
  },
  "required": [
   "result",
   "checks"
  ],
  "title": "DecisionRule",
  "type": "object"
 },
 "GateConfig": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "additionalProperties": false,
  "description": "게이트 설정(GateConfig.record()). 환경 변수는 main.py만 읽고 이 모양으로 넘긴다. 역할 집합은 배열이다",
  "properties": {
   "action_rules_version": {
    "type": [
     "string",
     "null"
    ]
   },
   "activate_roles": {
    "items": {
     "minLength": 1,
     "type": "string"
    },
    "type": "array",
    "uniqueItems": true
   },
   "approver_role": {
    "minLength": 1,
    "type": "string"
   },
   "approver_roles": {
    "items": {
     "minLength": 1,
     "type": "string"
    },
    "type": "array",
    "uniqueItems": true
   },
   "max_pending_submissions": {
    "minimum": 1,
    "type": [
     "integer",
     "null"
    ]
   },
   "model_runtime_version": {
    "description": "읽지 못하면 null(승인 때 거부, RuntimeVersions)",
    "type": [
     "string",
     "null"
    ]
   },
   "rollback_roles": {
    "items": {
     "minLength": 1,
     "type": "string"
    },
    "type": "array",
    "uniqueItems": true
   },
   "service_roles": {
    "items": {
     "minLength": 1,
     "type": "string"
    },
    "type": "array",
    "uniqueItems": true
   },
   "submit_roles": {
    "items": {
     "minLength": 1,
     "type": "string"
    },
    "type": "array",
    "uniqueItems": true
   },
   "supported_runtime_contract_version": {
    "minimum": 1,
    "type": "integer"
   }
  },
  "required": [
   "supported_runtime_contract_version",
   "action_rules_version",
   "model_runtime_version",
   "submit_roles",
   "activate_roles",
   "rollback_roles",
   "service_roles",
   "approver_roles",
   "max_pending_submissions",
   "approver_role"
  ],
  "title": "GateConfig",
  "type": "object"
 },
 "IdString": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "description": "ID 문자열(계약 _common의 Id와 같은 패턴). X-Openit-User와 시도 기록의 대상 ID가 쓴다",
  "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
  "title": "IdString",
  "type": "string"
 },
 "ModelInventory": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "additionalProperties": false,
  "description": "I3에서 읽은 감시 대상: 패키지 버전 요약들과 건너뛴 항목",
  "properties": {
   "skipped": {
    "items": {
     "type": "string"
    },
    "type": "array"
   },
   "versions": {
    "items": {
     "$ref": "ModelVersionSummary.json"
    },
    "type": "array"
   }
  },
  "required": [
   "versions",
   "skipped"
  ],
  "title": "ModelInventory",
  "type": "object"
 },
 "ModelVersionSummary": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "additionalProperties": false,
  "description": "패키지 한 버전의 요약(I3 매니페스트에서 뽑은 것)",
  "properties": {
   "created_at": {
    "minLength": 1,
    "type": "string"
   },
   "package_id": {
    "minLength": 1,
    "type": "string"
   },
   "ref": {
    "description": "계약 ArtifactRef(id, version, content_hash). 모양은 계약이 정본이고 경계에서 계약으로 검증된 값이다",
    "required": [
     "id",
     "version",
     "content_hash"
    ],
    "type": "object"
   },
   "role": {
    "enum": [
     "LORA_A",
     "LORA_B"
    ]
   }
  },
  "required": [
   "package_id",
   "role",
   "created_at",
   "ref"
  ],
  "title": "ModelVersionSummary",
  "type": "object"
 },
 "PageQuery": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "additionalProperties": false,
  "description": "목록 쿼리의 쪽 크기(1~500). HTTP 면이 정수로 바꾼 값을 검사한다",
  "properties": {
   "limit": {
    "maximum": 500,
    "minimum": 1,
    "type": "integer"
   }
  },
  "required": [
   "limit"
  ],
  "title": "PageQuery",
  "type": "object"
 },
 "QuestionInventory": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "additionalProperties": false,
  "description": "I1에서 읽은 감시 대상: 문항 세트별 검수 완료 최신 참조와 건너뛴 항목",
  "properties": {
   "question_sets": {
    "items": {
     "description": "계약 ArtifactRef(id, version, content_hash). 모양은 계약이 정본이고 경계에서 계약으로 검증된 값이다",
     "required": [
      "id",
      "version",
      "content_hash"
     ],
     "type": "object"
    },
    "type": "array"
   },
   "skipped": {
    "items": {
     "type": "string"
    },
    "type": "array"
   }
  },
  "required": [
   "question_sets",
   "skipped"
  ],
  "title": "QuestionInventory",
  "type": "object"
 },
 "RefFilter": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "additionalProperties": false,
  "description": "부분 참조 필터(id 필수, version·content_hash 선택). HTTP 조회는 해시 없이 id·version만 준다",
  "properties": {
   "content_hash": {
    "type": "string"
   },
   "id": {
    "minLength": 1,
    "type": "string"
   },
   "version": {
    "type": [
     "string",
     "integer"
    ]
   }
  },
  "required": [
   "id"
  ],
  "title": "RefFilter",
  "type": "object"
 },
 "ReleaseBuildInput": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "additionalProperties": false,
  "description": "릴리스 매니페스트 구성 입력",
  "properties": {
   "adapters": {
    "additionalProperties": false,
    "properties": {
     "LORA_A": {
      "$ref": "AdapterInfo.json"
     },
     "LORA_B": {
      "$ref": "AdapterInfo.json"
     }
    },
    "required": [
     "LORA_A",
     "LORA_B"
    ],
    "type": "object"
   },
   "created_at": {
    "minLength": 1,
    "type": "string"
   },
   "gate_decision_id": {
    "pattern": "^gd-.",
    "type": "string"
   },
   "release_id": {
    "pattern": "^rel-.",
    "type": "string"
   },
   "subject": {
    "description": "판정 대상 조합(계약 GateSubmission.subject). 세 참조의 모양은 계약이 정본이다",
    "required": [
     "question_set",
     "lora_a",
     "lora_b"
    ],
    "type": "object"
   },
   "versions": {
    "$ref": "RuntimeVersions.json"
   }
  },
  "required": [
   "release_id",
   "created_at",
   "gate_decision_id",
   "subject",
   "versions",
   "adapters"
  ],
  "title": "ReleaseBuildInput",
  "type": "object"
 },
 "RequestObject": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "description": "쓰기 요청 본문은 JSON 객체여야 한다(HTTP 면)",
  "title": "RequestObject",
  "type": "object"
 },
 "ResolvedPolicy": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "additionalProperties": false,
  "description": "읽어 낸 게이트 정책: 판정 기록에 적는 버전과 검사별 인자(임계치는 없다, D53)",
  "properties": {
   "check_config": {
    "type": "object"
   },
   "version": {
    "minLength": 1,
    "type": "string"
   }
  },
  "required": [
   "version",
   "check_config"
  ],
  "title": "ResolvedPolicy",
  "type": "object"
 },
 "RetentionPolicy": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "additionalProperties": false,
  "description": "보존 정책. 무기한 보존은 허용하지 않는다(유한한 정수만, D88 U4)",
  "properties": {
   "attempts_days": {
    "minimum": 1,
    "type": "integer"
   },
   "keep_releases": {
    "minimum": 0,
    "type": "integer"
   },
   "rejected_days": {
    "minimum": 1,
    "type": "integer"
   }
  },
  "required": [
   "keep_releases",
   "rejected_days",
   "attempts_days"
  ],
  "title": "RetentionPolicy",
  "type": "object"
 },
 "ReviewReportInput": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "additionalProperties": false,
  "description": "승인 요청 보고서 입력: 후보의 평가 보고서와 활성 릴리스의 기준 보고서(역할별 EvaluationReport)",
  "properties": {
   "active_release_id": {
    "type": [
     "string",
     "null"
    ]
   },
   "baseline": {
    "additionalProperties": {
     "type": "object"
    },
    "propertyNames": {
     "enum": [
      "LORA_A",
      "LORA_B"
     ]
    },
    "type": "object"
   },
   "candidate": {
    "additionalProperties": {
     "type": "object"
    },
    "propertyNames": {
     "enum": [
      "LORA_A",
      "LORA_B"
     ]
    },
    "type": "object"
   },
   "checks": {
    "items": {
     "type": "object"
    },
    "type": "array"
   },
   "generated_at": {
    "minLength": 1,
    "type": "string"
   },
   "subject": {
    "description": "판정 대상 조합(계약 GateSubmission.subject). 세 참조의 모양은 계약이 정본이다",
    "required": [
     "question_set",
     "lora_a",
     "lora_b"
    ],
    "type": "object"
   },
   "submission_id": {
    "pattern": "^sub-",
    "type": "string"
   }
  },
  "required": [
   "submission_id",
   "subject",
   "generated_at",
   "checks",
   "candidate",
   "active_release_id",
   "baseline"
  ],
  "title": "ReviewReportInput",
  "type": "object"
 },
 "RoleClaim": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "additionalProperties": false,
  "description": "요청자가 주장하는 역할(비어 있지 않은 문자열). 허용 목록 대조는 설정 값이라 스키마 밖이다",
  "properties": {
   "requested_by_role": {
    "minLength": 1,
    "type": "string"
   }
  },
  "required": [
   "requested_by_role"
  ],
  "title": "RoleClaim",
  "type": "object"
 },
 "RuntimeVersions": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "additionalProperties": false,
  "description": "릴리스 매니페스트에 적을 이웃 버전. 읽지 못했거나 'unset'이면 거짓 값이 계약 필드에 들어가므로 거부한다(fail-closed)",
  "properties": {
   "action_rules_version": {
    "pattern": "^(?!\\s*$)(?!\\s*unset\\s*$).",
    "type": "string"
   },
   "model_runtime_version": {
    "pattern": "^(?!\\s*$)(?!\\s*unset\\s*$).",
    "type": "string"
   },
   "supported_runtime_contract_version": {
    "minimum": 1,
    "type": "integer"
   }
  },
  "required": [
   "supported_runtime_contract_version",
   "action_rules_version",
   "model_runtime_version"
  ],
  "title": "RuntimeVersions",
  "type": "object"
 },
 "StorePageArgs": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "additionalProperties": false,
  "description": "저장소 목록 조회의 쪽 나누기 인자(SDK 직접 호출 포함, 최대 1000)",
  "properties": {
   "cursor": {
    "pattern": "^-?[0-9]+$",
    "type": "string"
   },
   "limit": {
    "maximum": 1000,
    "minimum": 1,
    "type": "integer"
   }
  },
  "required": [
   "limit"
  ],
  "title": "StorePageArgs",
  "type": "object"
 },
 "SubjectData": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "additionalProperties": false,
  "description": "판정 대상의 내용 전부(문항 쪽 + 모델 쪽 + 문진 스키마 위반 목록). 자동 검사의 공통 입력 재료",
  "properties": {
   "bank": {
    "description": "문항 세트(계약 QuestionBank). 읽지 못했으면 null",
    "type": [
     "object",
     "null"
    ]
   },
   "bank_violations": {
    "items": {
     "type": "string"
    },
    "type": "array"
   },
   "eval_reports": {
    "additionalProperties": {
     "type": "object"
    },
    "propertyNames": {
     "enum": [
      "LORA_A",
      "LORA_B"
     ]
    },
    "type": "object"
   },
   "manifests": {
    "additionalProperties": {
     "type": "object"
    },
    "propertyNames": {
     "enum": [
      "LORA_A",
      "LORA_B"
     ]
    },
    "type": "object"
   },
   "problems": {
    "items": {
     "type": "string"
    },
    "type": "array"
   },
   "report_problems": {
    "items": {
     "type": "string"
    },
    "type": "array"
   },
   "reviews": {
    "items": {
     "type": "object"
    },
    "type": "array"
   },
   "reviews_ok": {
    "type": "boolean"
   },
   "subject": {
    "description": "판정 대상 조합(계약 GateSubmission.subject). 세 참조의 모양은 계약이 정본이다",
    "required": [
     "question_set",
     "lora_a",
     "lora_b"
    ],
    "type": "object"
   }
  },
  "required": [
   "subject",
   "bank",
   "bank_violations",
   "reviews",
   "reviews_ok",
   "manifests",
   "eval_reports",
   "report_problems",
   "problems"
  ],
  "title": "SubjectData",
  "type": "object"
 },
 "SubjectModels": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "additionalProperties": false,
  "description": "I3에서 읽은 판정 대상의 모델 쪽 내용: 역할별 매니페스트와 검증을 통과한 평가 보고서",
  "properties": {
   "eval_reports": {
    "additionalProperties": {
     "type": "object"
    },
    "propertyNames": {
     "enum": [
      "LORA_A",
      "LORA_B"
     ]
    },
    "type": "object"
   },
   "manifests": {
    "additionalProperties": {
     "type": "object"
    },
    "propertyNames": {
     "enum": [
      "LORA_A",
      "LORA_B"
     ]
    },
    "type": "object"
   },
   "problems": {
    "items": {
     "type": "string"
    },
    "type": "array"
   },
   "report_problems": {
    "items": {
     "type": "string"
    },
    "type": "array"
   }
  },
  "required": [
   "manifests",
   "eval_reports",
   "report_problems",
   "problems"
  ],
  "title": "SubjectModels",
  "type": "object"
 },
 "SubjectQuestions": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "additionalProperties": false,
  "description": "I1에서 읽은 판정 대상의 문항 쪽 내용",
  "properties": {
   "bank": {
    "description": "문항 세트(계약 QuestionBank). 읽지 못했으면 null",
    "type": [
     "object",
     "null"
    ]
   },
   "problems": {
    "items": {
     "type": "string"
    },
    "type": "array"
   },
   "reviews": {
    "items": {
     "type": "object"
    },
    "type": "array"
   },
   "reviews_ok": {
    "type": "boolean"
   }
  },
  "required": [
   "bank",
   "reviews",
   "reviews_ok",
   "problems"
  ],
  "title": "SubjectQuestions",
  "type": "object"
 },
 "SubmissionListQuery": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "additionalProperties": false,
  "description": "제출 목록 조회 조건(GET /v1/submissions). 결정 전 상태 필터와 쪽 나누기",
  "properties": {
   "cursor": {
    "description": "마지막으로 돌려준 접수 순번",
    "pattern": "^[0-9]+$",
    "type": "string"
   },
   "limit": {
    "default": 100,
    "maximum": 500,
    "minimum": 1,
    "type": "integer"
   },
   "status": {
    "enum": [
     "PENDING_CHECK",
     "AWAITING_APPROVAL",
     "REJECTED_AUTO",
     "REJECTED_HUMAN",
     "PASSED"
    ]
   }
  },
  "required": [],
  "title": "SubmissionListQuery",
  "type": "object"
 },
 "SubmitRequest": {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "additionalProperties": false,
  "description": "접수 입력: 판정 대상 조합과 요청 역할(I5 submit_candidate)",
  "properties": {
   "requested_by_role": {
    "minLength": 1,
    "type": "string"
   },
   "subject": {
    "description": "판정 대상 조합(계약 GateSubmission.subject). 세 참조의 모양은 계약이 정본이다",
    "required": [
     "question_set",
     "lora_a",
     "lora_b"
    ],
    "type": "object"
   }
  },
  "required": [
   "subject",
   "requested_by_role"
  ],
  "title": "SubmitRequest",
  "type": "object"
 }
}''')
_cache: dict = {}


def _registry(docs: dict, suffix: str) -> Registry:
    return Registry().with_resources([(f"{n}{suffix}", Resource.from_contents(d, default_specification=DRAFT202012)) for n, d in docs.items()])


def _message(name: str, v, record) -> str:
    errs = sorted(v.iter_errors(record), key=lambda e: list(e.path))
    if not errs:
        return ""
    return f"{name} 스키마 위반: " + "; ".join(f"{'/'.join(map(str, e.path)) or '.'}: {e.message}" for e in errs[:5])


def validate(name: str, record, *, code: str = "INVALID_RECORD", details: Optional[dict] = None) -> None:
    """영역 내부 타입 검사. 위반이면 BoundaryError(code)."""
    v = _cache.get(("i", name))
    if v is None:
        if name not in _INTERNAL:
            raise BoundaryError("INVALID_RECORD", f"알 수 없는 내부 스키마: {name}")
        v = _cache[("i", name)] = jsonschema.Draft202012Validator({"$ref": f"{name}.json"}, registry=_registry(_INTERNAL, ".json"))
    msg = _message(name, v, record)
    if msg:
        raise BoundaryError(code, msg, details)


def validate_contract(name: str, record) -> None:
    """boundary-contracts 계약 타입 검사. 위반이면 INVALID_RECORD."""
    v = _cache.get(("c", name))
    if v is None:
        d = boundary_contracts.schema_dir()
        docs = {p.name[: -len(".schema.json")]: boundary_contracts.load_schema(p.name[: -len(".schema.json")]) for p in sorted(d.glob("*.schema.json"))}
        if name not in docs:
            raise BoundaryError("INVALID_RECORD", f"알 수 없는 스키마: {name}")
        v = _cache[("c", name)] = jsonschema.Draft202012Validator({"$ref": f"{name}.schema.json"}, registry=_registry(docs, ".schema.json"))
    msg = _message(name, v, record)
    if msg:
        raise BoundaryError("INVALID_RECORD", msg)
