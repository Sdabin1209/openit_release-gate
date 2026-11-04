"""선언(DatabaseDeclaration)에서 이미지 안 초기화가 쓰는 파일을 만든다: accounts.tsv(이름 데이터베이스 권한), pg_hba.conf, port.
pg_hba.conf: 선언한 계정만 선언한 데이터베이스에, TLS(hostssl)와 scram-sha-256으로만 닿는다. 로컬 소켓도 scram이다. trust·평문(host)·슈퍼유저의 원격 접속은 없다."""
import json
import pathlib
import re
import sys

NAME = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
d = json.load(open(sys.argv[1], encoding="utf-8"))
out = pathlib.Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
rows, hba = [], ["# release-gate-db: 생성 파일(db/gen.py). trust 없음, 평문 없음.", "local all all scram-sha-256"]
for a in d["accounts"]:
    assert NAME.match(a["name"]) and NAME.match(a["database"]), a      # 셸에서 식별자로 그대로 쓰므로 선언의 패턴을 한 번 더 확인한다
    assert a["privilege"] in ("owner", "dml"), a
    rows.append("\t".join((a["name"], a["database"], a["privilege"])))
    hba += [f"hostssl {a['database']} {a['name']} 0.0.0.0/0 scram-sha-256", f"hostssl {a['database']} {a['name']} ::/0 scram-sha-256"]
(out / "accounts.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
(out / "pg_hba.conf").write_text("\n".join(hba) + "\n", encoding="utf-8")
(out / "port").write_text(str(d["port"]), encoding="utf-8")
