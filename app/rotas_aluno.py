from __future__ import annotations

import os
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException
from supabase import create_client, Client

# Mantém a mesma estratégia do projeto: backend acessa Supabase com key do servidor
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    # Evita erro silencioso em deploy
    raise RuntimeError("Variáveis SUPABASE_URL/SUPABASE_KEY não configuradas")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

router = APIRouter(prefix="/aluno", tags=["aluno"])


def _get_bearer_token(authorization: Optional[str]) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Token ausente")
    parts = authorization.split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Formato do token inválido")
    return parts[1].strip()


def _slugify(value: str) -> str:
    value = (value or "").strip()
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_user_id_from_token(token: str) -> str:
    try:
        user = supabase.auth.get_user(token)
        return user.user.id
    except Exception:
        raise HTTPException(status_code=401, detail="Token inválido ou expirado")


def _get_aluno_context(token: str) -> Dict[str, Any]:
    user_id = _get_user_id_from_token(token)

    aluno_resp = (
        supabase.table("tb_alunos")
        .select("id_aluno, nome_completo")
        .eq("user_id", user_id)
        .execute()
    )

    if not aluno_resp.data:
        raise HTTPException(status_code=403, detail="Conta não vinculada a um aluno")

    aluno = aluno_resp.data[0]

    # Todas as matrículas do aluno (da mais recente para a mais antiga)
    matriculas_resp = (
        supabase.table("tb_matriculas")
        .select("id_matricula, codigo_turma, status_financeiro, data_matricula")
        .eq("id_aluno", aluno["id_aluno"])
        .order("data_matricula", desc=True)
        .order("id_matricula", desc=True)
        .execute()
    )

    matriculas = matriculas_resp.data or []

    # Lista de códigos de turma (únicos, preservando ordem)
    codigos: List[str] = []
    for m in matriculas:
        c = m.get("codigo_turma")
        if c is None:
            continue
        c = str(c).strip()
        if c:
            codigos.append(c)

    codigos = list(dict.fromkeys(codigos))

    turma_by_codigo: Dict[str, Any] = {}
    if codigos:
        turmas_resp = (
            supabase.table("tb_turmas")
            .select(
                "codigo_turma, nome_curso, id_professor, data_inicio, "
                "qtd_aulas, status, tipo_turma"
            )
            .in_("codigo_turma", codigos)
            .execute()
        )

        for t in turmas_resp.data or []:
            turma_by_codigo[str(t.get("codigo_turma")).strip()] = t

    # Monta cursos permitidos (um por curso), usando a matrícula mais recente daquele curso
    cursos_by_slug: Dict[str, Any] = {}
    for m in matriculas:
        codigo = m.get("codigo_turma")
        if codigo is None:
            continue
        codigo = str(codigo).strip()
        turma = turma_by_codigo.get(codigo)
        if not turma:
            continue

        curso_nome = (turma.get("nome_curso") or "").strip()
        slug = _slugify(curso_nome)
        if not slug:
            continue

        # Como matriculas já vem ordenado desc, a primeira ocorrência do slug é a mais recente
        if slug not in cursos_by_slug:
            data_inicio = turma.get("data_inicio") or "2024-01-01"
            cursos_by_slug[slug] = {
                "id": slug,
                "data_inicio": data_inicio,
                "codigo_turma": turma.get("codigo_turma"),
                "curso_nome": curso_nome,
                "turma": turma,
                "matricula": m,
            }

    cursos = list(cursos_by_slug.values())
    cursos.sort(key=lambda x: (x.get("curso_nome") or x.get("id") or ""))

    return {
        "user_id": user_id,
        "id_aluno": aluno["id_aluno"],
        "nome": aluno.get("nome_completo") or "",
        "matriculas": matriculas,
        "turmas_by_codigo": turma_by_codigo,
        "cursos_by_slug": cursos_by_slug,
        "cursos": cursos,
    }



def _fetch_cursos_didaticos() -> List[Dict[str, Any]]:
    """
    Busca cursos com módulos e aulas aninhados, e normaliza a ordenação.
    """
    resp = supabase.table("cursos")        .select("*, modulos(*, aulas(*))")        .order("ordem")        .execute()

    cursos = resp.data or []
    for c in cursos:
        c["slug"] = _slugify(c.get("slug") or c.get("titulo") or "")
        for m in c.get("modulos", []) or []:
            m["aulas"] = sorted(m.get("aulas", []) or [], key=lambda x: x.get("ordem", 0))
        c["modulos"] = sorted(c.get("modulos", []) or [], key=lambda x: x.get("ordem", 0))
    return cursos


def _flatten_aulas(curso: Dict[str, Any]) -> List[Dict[str, Any]]:
    aulas = []
    for m in curso.get("modulos", []) or []:
        for a in m.get("aulas", []) or []:
            aulas.append({
                "id": a.get("id"),
                "titulo": a.get("titulo"),
                "modulo_id": m.get("id"),
                "modulo_titulo": m.get("titulo"),
                "ordem_modulo": m.get("ordem", 0),
                "ordem_aula": a.get("ordem", 0),
            })
    aulas.sort(key=lambda x: (x["ordem_modulo"], x["ordem_aula"]))
    # Index global para cálculo de liberação
    for idx, a in enumerate(aulas, start=1):
        a["ordem_global"] = idx
    return aulas


@router.get("/meus-cursos")
def meus_cursos(authorization: Optional[str] = Header(None)):
    token = _get_bearer_token(authorization)
    ctx = _get_aluno_context(token)

    # Adicionamos o nome do aluno no retorno
    return {
        "nome": ctx.get("nome", "Aluno"),
        "cursos": ctx.get("cursos", [])
    }

    



@router.get("/curso/{curso_slug}/estrutura")
def curso_estrutura(curso_slug: str, authorization: Optional[str] = Header(None)):
    """
    Retorna a estrutura do curso (módulos/aulas) + metadados úteis.
    """
    token = _get_bearer_token(authorization)
    ctx = _get_aluno_context(token)

    slug_req = _slugify(curso_slug)
    info = (ctx.get("cursos_by_slug") or {}).get(slug_req)
    if not info:
        raise HTTPException(status_code=403, detail="Curso não permitido")

    turma = info["turma"]
    slug_matricula = slug_req

    cursos = _fetch_cursos_didaticos()

    # Encontra curso didático por slug/título
    curso = next(
        (c for c in cursos if _slugify(c.get("slug") or c.get("titulo") or "") == slug_matricula),
        None,
    )
    if not curso:
        raise HTTPException(status_code=404, detail="Curso não encontrado no didático")

    aulas_flat = _flatten_aulas(curso)
    total_aulas = len(aulas_flat)

    # Progresso automático: 1 aula liberada a cada 7 dias a partir de data_inicio
    try:
        raw_inicio = turma.get("data_inicio") or "2024-01-01"
        if isinstance(raw_inicio, str):
            di = datetime.fromisoformat(raw_inicio.replace("Z", "+00:00"))
        else:
            di = datetime.combine(raw_inicio, datetime.min.time())
        if di.tzinfo is None:
            di = di.replace(tzinfo=timezone.utc)
        hoje = datetime.now(timezone.utc)
        dias_passados = max(0, (hoje - di).days)
    except Exception:
        dias_passados = 0

    aulas_liberadas = (dias_passados // 7) + 1
    if aulas_liberadas > total_aulas:
        aulas_liberadas = total_aulas

    # Marca aulas liberadas
    for m in curso.get("modulos", []) or []:
        for a in m.get("aulas", []) or []:
            ord_global = next((x["ordem_global"] for x in aulas_flat if x["id"] == a.get("id")), None)
            a["ordem_global"] = ord_global
            a["liberada"] = bool(ord_global and ord_global <= aulas_liberadas)

    curso_out = dict(curso)
    curso_out["aulas_total"] = total_aulas
    curso_out["aulas_liberadas"] = aulas_liberadas
    curso_out["dias_passados"] = dias_passados
    curso_out["data_inicio"] = turma.get("data_inicio")
    curso_out["codigo_turma"] = turma.get("codigo_turma")
    curso_out["curso_nome"] = (turma.get("nome_curso") or "").strip()

    return curso_out



@router.get("/aula/{id_aula}")
def obter_aula(id_aula: int, authorization: Optional[str] = Header(None)):
    token = _get_bearer_token(authorization)
    ctx = _get_aluno_context(token)

    try:
        aula_resp = (
            supabase.table("aulas")
            .select("*")  # <- ajuda a não quebrar por coluna errada (temporário)
            .eq("id", id_aula)
            .limit(1)
            .execute()
        )
        if not aula_resp.data:
            raise HTTPException(status_code=404, detail="Aula não encontrada")

        aula = aula_resp.data[0]
        conteudo = aula.get("conteudo") or ""

        # aceita modulo_id ou id_modulo (caso seu banco use outro nome)
        modulo_id = aula.get("modulo_id") or aula.get("id_modulo")
        if not modulo_id:
            raise HTTPException(status_code=500, detail="Aula sem modulo_id/id_modulo no banco")

        mod_resp = (
            supabase.table("modulos")
            .select("*")  # <- temporário
            .eq("id", modulo_id)
            .limit(1)
            .execute()
        )
        if not mod_resp.data:
            raise HTTPException(status_code=500, detail="Módulo inválido para esta aula")

        mod = mod_resp.data[0]
        curso_id = mod.get("curso_id") or mod.get("id_curso")
        if not curso_id:
            raise HTTPException(status_code=500, detail="Módulo sem curso_id/id_curso no banco")

        curso_resp = (
            supabase.table("cursos")
            .select("*")  # <- temporário
            .eq("id", curso_id)
            .limit(1)
            .execute()
        )
        if not curso_resp.data:
            raise HTTPException(status_code=500, detail="Curso não encontrado para esta aula")

        curso_row = curso_resp.data[0]
        curso_slug_aula = _slugify(curso_row.get("titulo") or "")

        info = (ctx.get("cursos_by_slug") or {}).get(curso_slug_aula)
        if not info:
            raise HTTPException(status_code=403, detail="Curso não permitido")

        turma = info["turma"]
        id_professor = turma.get("id_professor")

        if id_professor:
            pers_resp = (
                supabase.table("conteudos_personalizados")
                .select("conteudo")
                .eq("id_aula", id_aula)
                .eq("id_professor", id_professor)
                .limit(1)
                .execute()
            )
            if pers_resp.data and pers_resp.data[0].get("conteudo"):
                conteudo = pers_resp.data[0]["conteudo"]

        return {
            "id": aula.get("id"),
            "titulo": aula.get("titulo"),
            "conteudo": conteudo,
            "updated_at": _now_iso(),
        }

    except HTTPException:
        raise
    except Exception as e:
        # Isso vai aparecer no Render Logs:
        print("ERRO obter_aula:", repr(e))
        raise HTTPException(status_code=500, detail=f"Erro interno obter_aula: {e}")

from pydantic import BaseModel

class PerfilUpdate(BaseModel):
    nome_completo: Optional[str] = None
    telefone: Optional[str] = None
    email: Optional[str] = None

class SenhaUpdate(BaseModel):
    senha_atual: Optional[str] = None
    senha_nova: str


@router.get("/perfil")
def get_perfil(authorization: Optional[str] = Header(None)):
    token = _get_bearer_token(authorization)
    user_id = _get_user_id_from_token(token)

    # pega email do auth
    try:
        user = supabase.auth.get_user(token)
        email = getattr(user.user, "email", None)
    except Exception:
        email = None

    # pega dados do aluno (select * pra não quebrar se seu schema variar)
    aluno_resp = (
        supabase.table("tb_alunos")
        .select("*")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if not aluno_resp.data:
        raise HTTPException(status_code=404, detail="Aluno não encontrado")

    aluno = aluno_resp.data[0]
    return {
        "email": email,
        "nome_completo": aluno.get("nome_completo") or "",
        "telefone": aluno.get("telefone") or "",
        # se você tiver mais colunas, pode expor aqui:
        # "cpf": aluno.get("cpf") or "",
        # "data_nascimento": aluno.get("data_nascimento") or "",
    }


@router.put("/perfil")
def update_perfil(payload: PerfilUpdate, authorization: Optional[str] = Header(None)):
    token = _get_bearer_token(authorization)
    user_id = _get_user_id_from_token(token)

    # 1) atualiza tabela tb_alunos (somente campos enviados)
    update_data: Dict[str, Any] = {}
    if payload.nome_completo is not None:
        update_data["nome_completo"] = payload.nome_completo.strip()
    if payload.telefone is not None:
        update_data["telefone"] = payload.telefone.strip()

    if update_data:
        supabase.table("tb_alunos").update(update_data).eq("user_id", user_id).execute()

    # 2) atualiza email no auth (se enviado)
    if payload.email is not None and payload.email.strip():
        try:
            supabase.auth.admin.update_user_by_id(user_id, {"email": payload.email.strip()})
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Erro ao atualizar e-mail: {e}")

    return {"ok": True}


@router.put("/senha")
def update_senha(payload: SenhaUpdate, authorization: Optional[str] = Header(None)):
    token = _get_bearer_token(authorization)
    user_id = _get_user_id_from_token(token)

    # (opcional) valida senha atual
    if payload.senha_atual:
        try:
            user = supabase.auth.get_user(token)
            email = getattr(user.user, "email", None)
            if not email:
                raise HTTPException(status_code=400, detail="Email do usuário não encontrado para validar senha atual")

            # tenta login com senha atual
            supabase.auth.sign_in_with_password({"email": email, "password": payload.senha_atual})
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=400, detail="Senha atual inválida")

    # troca senha (admin)
    try:
        supabase.auth.admin.update_user_by_id(user_id, {"password": payload.senha_nova})
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao atualizar senha: {e}")

    return {"ok": True}
