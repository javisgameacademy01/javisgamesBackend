"""
Rotas administrativas do sistema
"""
import os
from fastapi import APIRouter, HTTPException, Header, UploadFile, File, Form, Query, BackgroundTasks
from pydantic import BaseModel
from supabase import create_client, Client
from datetime import datetime, timedelta
import time
import json
import requests
import logging
import time
import io
from jinja2 import Template
from xhtml2pdf import pisa
from typing import Optional, List

# IMPORTAÇÕES CONSOLIDADAS E LIMPAS (Sem duplicatas)
from app.modelos import (
    FestaAniversarioCreate,
    FestaAniversarioUpdate,
    FuncionarioEdicaoData,
    PerfilUpdateData,
    ReposicaoEdicaoData,
    ReposicaoUpdate,
    TurmaData,
    NovoAlunoData,
    AlunoEdicaoData,
    ReposicaoData,
    ChatAdminReply,
    StatusUpdateData,
    NovoFuncionarioData,
    AulaConteudoData,
    MensagemDiretaData,
    MensagemGrupoData,
    LoginData,
    NovoUsuarioData,
    ItemChamada,
    AulaExperimentalCreate,
    AulaExperimentalUpdate
)

class ContratoData(BaseModel):
    # Campos que o Site e o Painel enviam
    curso: str
    aluno_nome: str
    aluno_cpf: Optional[str] = None
    aluno_nascimento: str
    whatsapp: Optional[str] = None
    email: Optional[str] = None
    cep: str
    endereco: str
    bairro: str
    escola_nome: Optional[str] = "Não Informada"
    escola_turno: Optional[str] = "N/A"
    escola_serie: Optional[str] = "N/A"
    
    # Novos Campos do Responsável (RG, etc.)
    responsavel_nome: Optional[str] = None
    responsavel_cpf: Optional[str] = None
    responsavel_parentesco: Optional[str] = None
    responsavel_rg: Optional[str] = None
    responsavel_rg_orgao: Optional[str] = None
    responsavel_rg_uf: Optional[str] = None
    responsavel_rg_data: Optional[str] = None
    profissao_responsavel: Optional[str] = None
    
    # Campos Financeiros (para o banco)
    valor_total: Optional[float] = 0.0
    parcelas: Optional[int] = 1
    vencimento: Optional[int] = 10



# Logger
logger = logging.getLogger(__name__)

# Router
router = APIRouter(prefix="/admin", tags=["admin"])

# Configuração do Supabase
url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(url, key)

# Credenciais Z-API
ZAPI_INSTANCE_ID = os.getenv("ZAPI_INSTANCE_ID")
ZAPI_TOKEN = os.getenv("ZAPI_TOKEN")
ZAPI_BASE_URL = f"https://api.z-api.io/instances/{ZAPI_INSTANCE_ID}/token/{ZAPI_TOKEN}/send-text"

# Dicionário para traduzir dia da semana
DIAS_MAPA = {
    "Segunda": 0, "Segunda-feira": 0, "Terça": 1, "Terça-feira": 1,
    "Quarta": 2, "Quarta-feira": 2, "Quinta": 3, "Quinta-feira": 3,
    "Sexta": 4, "Sexta-feira": 4, "Sábado": 5, "Sabado": 5, "Domingo": 6
}

MAPA_CURSOS = {
    "GAME PRO": "game-pro",
    "DESIGNER START": "designer-start",
    "GAME DEV": "game-dev",
}

# --- FUNÇÕES AUXILIARES ---

def _pode_cadastrar_aluno(ctx: dict) -> bool:
    return (ctx.get("nivel") == 3) or (ctx.get("nivel", 0) >= 8)

def _pode_editar_aula_experimental(ctx: dict) -> bool:
    return (ctx.get("nivel") == 3) or (ctx.get("nivel", 0) >= 8)

def verificar_permissao_repo(id_repo: str, ctx: dict) -> bool:
    """Verifica se o usuário é nível 8+ ou se foi ele quem criou a reposição."""
    if ctx.get("nivel", 0) >= 8:
        return True
    try:
        resp = supabase.table("tb_reposicoes").select("criado_por").eq("id", id_repo).single().execute()
        if resp.data and resp.data.get("criado_por") == ctx.get("user_id"):
            return True
        return False
    except:
        return False

def enviar_mensagem_zapi(telefone_destino: str, mensagem_texto: str):
    headers = {"Content-Type": "application/json"}
    payload = {"phone": telefone_destino, "message": mensagem_texto}
    try:
        response = requests.post(ZAPI_BASE_URL, json=payload, headers=headers)
        return response.status_code == 200
    except Exception as e:
        print(f"Erro Z-API: {e}")
        return False

def calcular_previsao(data_inicio_str: str, qtd: int):
    if not data_inicio_str or not qtd:
        return None
    try:
        dt_inicio = datetime.strptime(data_inicio_str, "%Y-%m-%d")
        dias_totais = (qtd - 1) * 7
        dt_fim = dt_inicio + timedelta(days=dias_totais)
        return dt_fim.strftime("%Y-%m-%d")
    except:
        return None

import time

def get_contexto_usuario(token: str):
    # Tentativa de retry para lidar com o erro "Resource temporarily unavailable" do Render
    for tentativa in range(3):
        try:
            # 1. Valida o Token no Supabase Auth
            user = supabase.auth.get_user(token)
            user_id = user.user.id
            
            # 2. Busca os dados do colaborador (ID numérico, Unidade, Nível)
            resp = supabase.table("tb_colaboradores")\
                .select("id_colaborador, id_unidade, id_cargo, tb_cargos!fk_cargos(nivel_acesso)")\
                .eq("user_id", user_id)\
                .single()\
                .execute()
                
            dados = resp.data
            if not dados:
                raise HTTPException(status_code=403, detail="Acesso não autorizado para este colaborador.")

            return {
                "user_id": user_id,
                "id_colaborador": dados['id_colaborador'],
                "id_unidade": dados['id_unidade'],
                "id_cargo": dados['id_cargo'],
                "nivel": dados['tb_cargos']['nivel_acesso']
            }
        except Exception as e:
            # Se for erro de recurso ocupado, espera 150ms e tenta de novo
            if "Resource temporarily unavailable" in str(e) and tentativa < 2:
                time.sleep(0.15)
                continue
            
            logger.error(f"Falha crítica no contexto do usuário (Tentativa {tentativa+1}): {str(e)}")
            raise HTTPException(status_code=401, detail="Sessão instável ou expirada. Por favor, faça login novamente.")
def obter_dados_token(authorization: str):
    try:
        token = authorization.split(" ")[1]
        return get_contexto_usuario(token)
    except Exception as e:
        logger.error(f"Erro ao decodificar token: {e}")
        raise HTTPException(status_code=401, detail="Sessão inválida ou expirada.")

def supabase_authed(token: str) -> Client:
    client = create_client(url, key)
    client.postgrest.auth(token)
    return client

def calcular_progresso_automatico(data_inicio_str, total_aulas):
    if not data_inicio_str:
        return 0
    inicio = datetime.strptime(data_inicio_str, "%Y-%m-%d")
    hoje = datetime.now()
    dias_passados = (hoje - inicio).days
    aulas_liberadas = (dias_passados // 7) + 1
    aulas_liberadas = min(aulas_liberadas, total_aulas)
    return round((aulas_liberadas / total_aulas) * 100)


# =========================================
# AULAS EXPERIMENTAIS
# =========================================

@router.get("/aulas-experimentais")
def listar_aulas_experimentais(q: Optional[str] = None, data: Optional[str] = None, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)

    try:
        query = supabase.table("tb_aulas_experimentais").select("*, tb_colaboradores(nome_completo)")
        if ctx['nivel'] < 9:
            query = query.eq("id_unidade", ctx['id_unidade'])
        if data:
            query = query.eq("data_aula", data)
        if q:
            termo = f"%{q.strip()}%"
            query = query.or_(f"aluno.ilike.{termo},responsavel.ilike.{termo},curso.ilike.{termo}")

        resp = query.order("data_aula", desc=True).execute()
        
        dados_formatados = []
        for r in (resp.data or []):
            colab = r.get("tb_colaboradores")
            r["vendedor_nome"] = colab.get("nome_completo") if isinstance(colab, dict) else "Não atribuído"
            dados_formatados.append(r)
            
        return dados_formatados
    except Exception as e:
        erro_banco = str(e)
        logger.error(f"Erro Supabase: {erro_banco}")
        if "id_unidade" in erro_banco:
            raise HTTPException(status_code=500, detail="Falta a coluna 'id_unidade' na tabela tb_aulas_experimentais no Supabase.")
        elif "tb_colaboradores" in erro_banco or "relationship" in erro_banco:
            raise HTTPException(status_code=500, detail="Falta a Chave Estrangeira em 'id_vendedor' ligando a tb_colaboradores.")
        elif "does not exist" in erro_banco and "tb_aulas_experimentais" in erro_banco:
            raise HTTPException(status_code=500, detail="A tabela 'tb_aulas_experimentais' não existe.")
        raise HTTPException(status_code=500, detail=f"Erro no banco: {erro_banco}")

@router.post("/aulas-experimentais")
def criar_aula_experimental(dados: AulaExperimentalCreate, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)

    if not _pode_editar_aula_experimental(ctx):
        raise HTTPException(status_code=403, detail="Acesso restrito.")

    try:
        payload = dados.model_dump(exclude_none=True)
        if not payload.get("id_vendedor"):
            payload["id_vendedor"] = ctx["id_colaborador"]
        payload["id_unidade"] = ctx["id_unidade"]

        resp = supabase.table("tb_aulas_experimentais").insert(payload).execute()
        return resp.data[0] if resp.data else {"message": "ok"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.patch("/aulas-experimentais/{id_aula}")
def editar_aula_experimental(id_aula: str, dados: dict, authorization: str = Header(None)): # Trocamos o modelo por dict para teste
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)

    if not _pode_editar_aula_experimental(ctx):
        raise HTTPException(status_code=403, detail="Acesso restrito.")
        
    try:
        # Filtramos apenas os campos que realmente existem na tb_aulas_experimentais
        # Isso evita enviar campos extras que quebram o banco
        campos_validos = [
            "responsavel", "contato1", "contato2", "aluno", 
            "data_aula", "horario", "curso", "origem", 
            "id_vendedor", "status_atendimento", "observacao"
        ]
        
        updates = {k: v for k, v in dados.items() if k in campos_validos}

        # Executa o update
        res = supabase.table("tb_aulas_experimentais").update(updates).eq("id", id_aula).execute()
        
        return {"message": "Atualizado com sucesso!"}
    except Exception as e:
        logger.error(f"Erro ao editar aula exp: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/aulas-experimentais/{id_aula}")
def deletar_aula_experimental(id_aula: str, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)

    if not _pode_editar_aula_experimental(ctx):
        raise HTTPException(status_code=403, detail="Acesso restrito.")

    registro = supabase.table("tb_aulas_experimentais").select("id_unidade").eq("id", id_aula).single().execute()
    if not registro.data: raise HTTPException(status_code=404, detail="Aula não encontrada.")
    if ctx["nivel"] < 9 and registro.data.get("id_unidade") != ctx["id_unidade"]:
        raise HTTPException(status_code=403, detail="Sem permissão para outra unidade.")

    try:
        supabase.table("tb_aulas_experimentais").delete().eq("id", id_aula).execute()
        return {"message": "Excluído!"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/aulas-experimentais/vendedores")
def listar_vendedores_aulas_experimentais(authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)

    if ctx["nivel"] != 3 and ctx["nivel"] < 8:
        raise HTTPException(status_code=403)

    q = supabase.table("tb_colaboradores").select("id_colaborador,nome_completo").eq("ativo", True)
    if ctx["nivel"] < 9:
        q = q.eq("id_unidade", ctx["id_unidade"])

    return q.order("nome_completo").execute().data or []


# =========================================
# GESTÃO DE EQUIPE / CARGOS
# =========================================

@router.get("/listar-cargos")
def admin_listar_cargos(authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    try:
        return supabase.table("tb_cargos").select("*").order("nivel_acesso").execute().data
    except: return []

@router.get("/listar-equipe")
def admin_listar_equipe(filtro_unidade: int | None = None, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)

    if ctx['nivel'] < 8: raise HTTPException(status_code=403, detail="Acesso restrito à Gerência.")

    try:
        query = supabase.table("tb_colaboradores").select("*, tb_cargos!fk_cargos(nome_cargo, nivel_acesso)").order("nome_completo")
        if ctx['nivel'] < 9: 
            query = query.eq("id_unidade", ctx['id_unidade'])
        else:
            if filtro_unidade: query = query.eq("id_unidade", filtro_unidade)
        return query.execute().data
    except Exception as e:
        print(f"Erro listar equipe: {e}")
        return []

@router.post("/cadastrar-funcionario")
def admin_cadastrar_funcionario(dados: NovoFuncionarioData, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)

    if ctx['nivel'] < 8: raise HTTPException(status_code=403, detail="Acesso restrito à Gerência.")

    try:
        user_auth = supabase.auth.admin.create_user({
            "email": dados.email,
            "password": dados.senha,
            "email_confirm": True 
        })
        new_user_id = user_auth.user.id

        supabase.table("tb_colaboradores").insert({
            "nome_completo": dados.nome.upper(),
            "email": dados.email,
            "telefone": dados.telefone,
            "id_cargo": dados.id_cargo,
            "user_id": new_user_id,
            "id_unidade": ctx['id_unidade'],
            "ativo": True
        }).execute()
        return {"message": "Funcionário cadastrado com sucesso!"}
    except Exception as e:
        print(f"Erro cadastro func: {e}")
        raise HTTPException(status_code=400, detail="Erro ao criar funcionário.")

@router.put("/editar-funcionario/{id_colaborador}")
def admin_editar_funcionario(id_colaborador: int, dados: FuncionarioEdicaoData, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)

    if ctx['nivel'] < 8: raise HTTPException(status_code=403, detail="Acesso restrito à Gerência.")

    try:
        updates = {}
        if dados.nome: updates["nome_completo"] = dados.nome.upper()
        if dados.telefone: updates["telefone"] = dados.telefone
        if dados.id_cargo: updates["id_cargo"] = dados.id_cargo
        if dados.ativo is not None: updates["ativo"] = dados.ativo

        if updates:
            supabase.table("tb_colaboradores").update(updates).eq("id_colaborador", id_colaborador).execute()
        return {"message": "Funcionário atualizado com sucesso!"}
    except Exception as e:
        print(f"Erro update func: {e}")
        raise HTTPException(status_code=400, detail="Erro ao atualizar funcionário.")


# =========================================
# GESTÃO DE TURMAS
# =========================================

@router.get("/gerenciar-turmas")
def admin_listar_turmas_completo(authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)

    try:
        query = supabase.table("tb_turmas").select("*, tb_colaboradores(nome_completo)").order("codigo_turma")
        if ctx['nivel'] < 9:
            query = query.eq("id_unidade", ctx['id_unidade'])
        return query.execute().data
    except Exception as e:
        print(f"Erro listar turmas: {e}")
        return []

@router.post("/salvar-turma")
def admin_salvar_turma(dados: TurmaData, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)

    try:
        previsao = calcular_previsao(dados.data_inicio, dados.qtd_aulas)
        supabase.table("tb_turmas").insert({
            "codigo_turma": dados.codigo.upper(),
            "id_professor": dados.id_professor,
            "nome_curso": dados.curso,
            "dia_semana": dados.dia_semana,
            "horario": dados.horario,
            "sala": dados.sala,
            "status": dados.status,
            "tipo_turma": dados.tipo,
            "data_inicio": dados.data_inicio,
            "qtd_aulas": dados.qtd_aulas,
            "data_termino_real": previsao,
            "data_termino_real": dados.data_termino_real,
            "id_unidade": ctx['id_unidade']
        }).execute()
        return {"message": "Turma criada!"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/editar-turma/{codigo_original}")
def admin_editar_turma(codigo_original: str, dados: TurmaData, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    try:
        previsao = calcular_previsao(dados.data_inicio, dados.qtd_aulas)
        supabase.table("tb_turmas").update({
            "id_professor": dados.id_professor,
            "nome_curso": dados.curso,
            "dia_semana": dados.dia_semana,
            "horario": dados.horario,
            "sala": dados.sala,
            "status": dados.status,
            "tipo_turma": dados.tipo,
            "data_inicio": dados.data_inicio,
            "qtd_aulas": dados.qtd_aulas,
            "data_termino_real": previsao,
            "data_termino_real": dados.data_termino_real
        }).eq("codigo_turma", codigo_original).execute()
        return {"message": "Turma atualizada!"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/listar-turmas")
def admin_listar_turmas(authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)
    try:
        query = supabase.table("tb_turmas").select("codigo_turma, nome_curso, dia_semana, horario")
        if ctx['nivel'] < 9: 
            query = query.eq("id_unidade", ctx['id_unidade'])
        return query.execute().data
    except: 
        return []


# =========================================
# DADOS DO FUNCIONÁRIO E PERFIL
# =========================================

@router.get("/meus-dados")
def get_dados_funcionario(authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401, detail="Token ausente")
    try:
        token = authorization.split(" ")[1]
        user = supabase.auth.get_user(token)
        user_id = user.user.id 
        
        response = supabase.table("tb_colaboradores")\
            .select("id_colaborador, nome_completo, telefone, email, id_cargo, id_unidade, tb_cargos(nome_cargo, nivel_acesso)")\
            .eq("user_id", user_id).execute()
            
        if not response.data:
            logger.error(f"Acesso negado: User ID {user_id} não encontrado na tb_colaboradores.")
            raise HTTPException(status_code=403, detail="Utilizador não vinculado como colaborador no banco de dados.")
            
        funcionario = response.data[0]
        cargo_data = funcionario.get('tb_cargos')
        
        if isinstance(cargo_data, list) and len(cargo_data) > 0:
            cargo_info = cargo_data[0]
        elif isinstance(cargo_data, dict):
            cargo_info = cargo_data
        else:
            cargo_info = {"nome_cargo": "Colaborador", "nivel_acesso": 1}

        return {
            "id_colaborador": funcionario.get('id_colaborador'),
            "user_id_auth": user_id,
            "nome": funcionario.get('nome_completo', 'Nome não definido'),
            "telefone": funcionario.get('telefone', ''),
            "email_contato": funcionario.get('email', ''),
            "cargo": cargo_info.get('nome_cargo', 'Staff'),
            "nivel": cargo_info.get('nivel_acesso', 1),
            "unidade": funcionario.get('id_unidade', 1)
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Erro crítico em meus-dados: {str(e)}")
        raise HTTPException(status_code=403, detail=f"Erro interno de permissão: {str(e)}")

@router.patch("/meus-dados")
def atualizar_meu_perfil(dados: PerfilUpdateData, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    try:
        token = authorization.split(" ")[1]
        user_id = supabase.auth.get_user(token).user.id
        updates = {}
        if dados.nome: updates["nome_completo"] = dados.nome.upper()
        if dados.telefone: updates["telefone"] = dados.telefone
        if dados.email_contato: updates["email"] = dados.email_contato
        if updates: supabase.table("tb_colaboradores").update(updates).eq("user_id", user_id).execute()
        
        auth_up = {}
        if dados.email_login: auth_up["email"] = dados.email_login
        if dados.nova_senha: auth_up["password"] = dados.nova_senha
            
        if auth_up: supabase.auth.admin.update_user_by_id(user_id, auth_up)
        return {"message": "Perfil atualizado!"}
    except Exception as e: 
        print(f"Erro ao atualizar perfil: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# =========================================
# CONTEÚDO DIDÁTICO
# =========================================

@router.get("/conteudo-didatico/cursos")
def admin_listar_cursos_didaticos(authorization: str = Header(None)):
    """Busca a árvore completa: Cursos -> Módulos -> Aulas"""
    if not authorization: raise HTTPException(status_code=401)
    try:
        # 1. Removi o .order() temporariamente para garantir que a consulta não trave
        resp = supabase.table("cursos").select("*, modulos(*, aulas(*))").execute()
        
        if not resp.data:
            logger.warning("⚠️ Tabela 'cursos' retornou vazia do Supabase.")
            return []
            
        dados = resp.data
        
        # 2. Ordenação manual para evitar erros de banco
        for curso in dados:
            if 'modulos' in curso and curso['modulos']:
                curso['modulos'] = sorted(curso['modulos'], key=lambda x: x.get('ordem', 0))
                for modulo in curso['modulos']:
                    if 'aulas' in modulo and modulo['aulas']:
                        modulo['aulas'] = sorted(modulo['aulas'], key=lambda x: x.get('ordem', 0))
        
        return dados
    except Exception as e:
        logger.error(f"❌ Erro ao carregar estrutura didática: {e}")
        # Retorna o erro real para o console do Render te ajudar
        return []

@router.get("/meus-cursos-permitidos")
def get_cursos_permitidos(authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    try:
        token = authorization.split(" ")[1]
        user_id = supabase.auth.get_user(token).user.id
        aluno_resp = supabase.table("tb_alunos").select("id_aluno").eq("user_id", user_id).execute()
        if not aluno_resp.data: return {"cursos": []}
            
        cursos_resp = supabase.table("cursos").select("id, titulo").eq("ativo", True).execute()
        cursos_permitidos = []
        for c in cursos_resp.data:
            slug = MAPA_CURSOS.get(c['titulo'].upper(), c['titulo'].lower().replace(" ", "-"))
            cursos_permitidos.append({"id": slug, "data_inicio": "2024-01-01"})
            
        return {"cursos": cursos_permitidos}
    except Exception as e:
        print(f"Erro cursos permitidos: {e}")
        return {"cursos": []}

@router.get("/conteudo-aula")
def get_conteudo_aula(titulo: str):
    try:
        res = supabase.table("aulas").select("*").ilike("titulo", f"%{titulo}%").execute()
        if not res.data:
            return {"titulo": titulo, "script": "Conteúdo em breve.", "codigo_exemplo": "# Em breve.", "desafio": "Aguarde."}
            
        aula = res.data[0]
        conteudo_raw = aula.get('conteudo') or ""
        
        script = "Bem-vindos!"
        codigo = ""
        desafio = "Pratique o que aprendeu."

        if "[SCRIPT]" in conteudo_raw:
            parts = conteudo_raw.split("[CODIGO]")
            script = parts[0].replace("[SCRIPT]", "").strip()
            if len(parts) > 1:
                if "[DESAFIO]" in parts[1]:
                    sub_parts = parts[1].split("[DESAFIO]")
                    codigo = sub_parts[0].strip()
                    desafio = sub_parts[1].strip()
                else:
                    codigo = parts[1].strip()
        
        return {"titulo": aula['titulo'], "script": script, "codigo_exemplo": codigo, "desafio": desafio}
    except Exception as e:
        print(f"Erro ao buscar conteúdo: {e}")
        return {"titulo": titulo, "script": "Erro ao carregar conteúdo."}

@router.get("/aula/{id_aula}/conteudo")
def get_aula_conteudo(id_aula: int, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)
    
    try:
        if ctx['nivel'] == 5:
            try:
                personalizado = supabase.table("conteudos_personalizados").select("conteudo").eq("id_aula", id_aula).eq("id_professor", ctx['id_colaborador']).maybe_single().execute()
                if personalizado.data and personalizado.data.get('conteudo'):
                    return {"html": personalizado.data['conteudo'], "tipo": "personalizado"}
            except Exception as e:
                print(f"Erro ao buscar personalizado: {e}")

        base = supabase.table("aulas").select("conteudo").eq("id", id_aula).maybe_single().execute()
        if base.data and base.data.get('conteudo'):
            return {"html": base.data['conteudo'], "tipo": "base"}
        
        return {"html": "", "tipo": "vazio"}
    except Exception as e:
        print(f"Erro buscar conteudo: {e}")
        return {"html": "", "tipo": "erro"}

@router.put("/aula/{id_aula}/salvar")
def salvar_aula_conteudo(id_aula: int, dados: AulaConteudoData, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)
    
    try:
        # NÍVEL 8+ (Coordenação, Direção, TI) -> Acesso Total
        if ctx['nivel'] >= 8:
            supabase.table("aulas").update({"conteudo": dados.conteudo}).eq("id", id_aula).execute()
            return {"message": "Conteúdo BASE atualizado (Modo Coordenação)."}
            
        # NÍVEL 5 (Professor) -> Salva apenas na sua versão personalizada
        elif ctx['nivel'] == 5:
            payload = {"id_aula": id_aula, "id_professor": ctx['id_colaborador'], "conteudo": dados.conteudo}
            supabase.table("conteudos_personalizados").upsert(payload, on_conflict="id_aula,id_professor").execute()
            return {"message": "Sua versão personalizada foi salva!"}
            
        # NÍVEL 3 (Comercial / Vendedor) -> Salva APENAS se for Aula Experimental
        elif ctx['nivel'] == 3:
            # 1. Descobre a qual módulo essa aula pertence
            aula_resp = supabase.table("aulas").select("modulo_id").eq("id", id_aula).single().execute()
            if not aula_resp.data:
                raise HTTPException(status_code=404, detail="Aula não encontrada.")
                
            # 2. Descobre a qual curso esse módulo pertence
            mod_resp = supabase.table("modulos").select("curso_id").eq("id", aula_resp.data["modulo_id"]).single().execute()
            
            # 3. Pega o nome do curso
            curso_resp = supabase.table("cursos").select("titulo").eq("id", mod_resp.data["curso_id"]).single().execute()
            nome_curso = curso_resp.data.get("titulo", "").upper()
            
            # 4. Verifica se é um curso experimental
            if "EXPERIMENTAL" in nome_curso or "TESTE" in nome_curso:
                supabase.table("aulas").update({"conteudo": dados.conteudo}).eq("id", id_aula).execute()
                return {"message": "Aula Experimental atualizada com sucesso!"}
            else:
                raise HTTPException(status_code=403, detail=f"Vendedores só podem editar Aulas Experimentais. O curso atual é: {nome_curso}")

        else:
            raise HTTPException(status_code=403, detail="Sem permissão para editar conteúdos.")
            
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao salvar: {str(e)}")

@router.get("/aula/{aula_id}")
def get_aula_por_id(aula_id: int, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    try:
        res = supabase.table("aulas").select("*").eq("id", aula_id).execute()
        if not res.data: raise HTTPException(status_code=404, detail="Aula não encontrada")
        return res.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =========================================
# CADASTRO E GESTÃO DE ALUNOS
# =========================================

@router.post("/cadastrar-aluno")
def admin_cadastrar_aluno(dados: NovoAlunoData, authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "): raise HTTPException(status_code=401)
    token = authorization.split(" ", 1)[1]
    ctx = get_contexto_usuario(token)

    if ctx["nivel"] != 3 and ctx["nivel"] < 8: raise HTTPException(status_code=403, detail="Acesso restrito.")

    new_user_id = None
    novo_id_aluno = None

    try:
        user_auth = supabase.auth.admin.create_user({"email": dados.email, "password": dados.senha, "email_confirm": True})
        new_user_id = user_auth.user.id

        nasc_formatado = dados.data_nascimento.replace("-", "")[:8] if dados.data_nascimento else None

        aluno_resp = supabase.table("tb_alunos").insert({
            "nome_completo": dados.nome, "cpf": dados.cpf, "email": dados.email, "celular": dados.celular,
            "telefone": dados.telefone, "data_nascimento": nasc_formatado, "user_id": new_user_id, "id_unidade": ctx["id_unidade"],
        }).execute()

        if not aluno_resp.data: raise Exception("Falha ao inserir aluno.")
        novo_id_aluno = aluno_resp.data[0]["id_aluno"]

        mat_resp = supabase.table("tb_matriculas").insert({
            "id_aluno": novo_id_aluno, "codigo_turma": dados.turma_codigo, "id_vendedor": ctx["id_colaborador"], "status_financeiro": "Ok"
        }).execute()

        if not mat_resp.data: raise Exception("Falha ao inserir matrícula.")
        return {"message": "Sucesso!", "id_aluno": novo_id_aluno, "user_id": new_user_id}
    except Exception as e:
        if novo_id_aluno:
            try: supabase.table("tb_alunos").delete().eq("id_aluno", novo_id_aluno).execute()
            except: pass
        if new_user_id:
            try: supabase.auth.admin.delete_user(new_user_id)
            except: pass
        raise HTTPException(status_code=400, detail=f"Erro cadastro: {str(e)}")

@router.get("/listar-alunos")
def admin_listar_alunos(authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)
    
    try:
        query = supabase.table("tb_alunos").select("*, tb_matriculas(id_matricula, codigo_turma, status_financeiro, tb_turmas(tipo_turma, dia_semana))")
        if ctx['nivel'] < 9: query = query.eq("id_unidade", ctx['id_unidade'])
        res = query.execute()
        
        dados = res.data
        for aluno in dados:
            if 'email_aluno' in aluno and not aluno.get('email'):
                aluno['email'] = aluno['email_aluno']
        return dados
    except Exception as e: 
        logger.error(f"Erro listar alunos: {e}")
        try: return supabase.table("tb_alunos").select("*").execute().data
        except: return []

@router.post("/criar-login-aluno")
def criar_login_aluno(dados: NovoUsuarioData, authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "): raise HTTPException(status_code=401)
    token = authorization.split(" ", 1)[1]
    ctx = get_contexto_usuario(token)

    if ctx["nivel"] != 3 and ctx["nivel"] < 8: raise HTTPException(status_code=403)

    new_user_id = None
    try:
        try: aluno = supabase.table("tb_alunos").select("id_aluno, user_id, id_unidade").eq("id_aluno", dados.id_aluno).single().execute()
        except: aluno = None

        if ctx["nivel"] < 9 and aluno.data.get("id_unidade") != ctx["id_unidade"]:
            raise HTTPException(status_code=403, detail="Sem permissão.")
        if not aluno or not aluno.data: raise HTTPException(status_code=404)
        if aluno.data.get("user_id"): raise HTTPException(status_code=400, detail="Aluno já possui login.")

        user_auth = supabase.auth.admin.create_user({"email": dados.email, "password": dados.senha, "email_confirm": True})
        new_user_id = user_auth.user.id

        up = supabase.table("tb_alunos").update({"email": dados.email, "user_id": new_user_id}).eq("id_aluno", dados.id_aluno).execute()
        if not up.data: raise Exception("Falha update.")
        return {"message": "Login criado com sucesso!", "user_id": new_user_id}
    except Exception as e:
        if new_user_id:
            try: supabase.auth.admin.delete_user(new_user_id)
            except: pass
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/editar-aluno/{id_aluno}")
def admin_editar_aluno(id_aluno: int, dados: AlunoEdicaoData, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)

    if ctx["nivel"] != 3 and ctx["nivel"] < 8: raise HTTPException(status_code=403)

    try:
        aluno_resp = supabase.table("tb_alunos").select("id_aluno,id_unidade,user_id,email").eq("id_aluno", id_aluno).single().execute()
        if not aluno_resp.data: raise HTTPException(status_code=404)
        aluno = aluno_resp.data

        if ctx["nivel"] < 9 and aluno.get("id_unidade") != ctx["id_unidade"]: raise HTTPException(status_code=403)

        updates = {}
        if getattr(dados, "nome", None): updates["nome_completo"] = dados.nome.upper()
        if getattr(dados, "cpf", None): updates["cpf"] = dados.cpf
        if getattr(dados, "celular", None): updates["celular"] = dados.celular
        if getattr(dados, "telefone", None): updates["telefone"] = dados.telefone

        novo_email = getattr(dados, "email", None)
        if novo_email:
            novo_email = novo_email.strip().lower()
            user_id = aluno.get("user_id")
            if not user_id: raise HTTPException(status_code=400, detail="Aluno não possui login.")
            email_anterior = (aluno.get("email") or "").strip().lower() or None
            
            supabase.auth.admin.update_user_by_id(str(user_id), {"email": novo_email})
            try:
                updates["email"] = novo_email
                supabase.table("tb_alunos").update(updates).eq("id_aluno", id_aluno).execute()
                updates.pop("email", None)
            except Exception as e_db:
                if email_anterior:
                    try: supabase.auth.admin.update_user_by_id(str(user_id), {"email": email_anterior})
                    except: pass
                raise HTTPException(status_code=400, detail=str(e_db))

        if updates: supabase.table("tb_alunos").update(updates).eq("id_aluno", id_aluno).execute()

        turma_codigo = getattr(dados, "turma_codigo", None)
        if turma_codigo:
            mats = supabase.table("tb_matriculas").select("id_matricula").eq("id_aluno", id_aluno).order("id_matricula", desc=True).limit(1).execute()
            if mats.data: supabase.table("tb_matriculas").update({"codigo_turma": turma_codigo}).eq("id_matricula", mats.data[0]["id_matricula"]).execute()
            else: supabase.table("tb_matriculas").insert({"id_aluno": id_aluno, "codigo_turma": turma_codigo, "id_vendedor": ctx["id_colaborador"], "status_financeiro": "Ok"}).execute()

        return {"message": "Aluno atualizado!"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# =========================================
# REPOSIÇÕES E AGENDA
# =========================================

@router.delete("/reposicao/{id_repo}")
def deletar_reposicao(id_repo: str, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)
    if not verificar_permissao_repo(id_repo, ctx): raise HTTPException(status_code=403, detail="Sem permissão.")
    try:
        supabase.table("tb_reposicoes").delete().eq("id", id_repo).execute()
        return {"message": "Excluída."}
    except: raise HTTPException(status_code=500)

@router.post("/agendar-reposicao")
def admin_reposicao(dados: ReposicaoData, authorization: str = Header(None)):
    if not authorization: 
        raise HTTPException(status_code=401, detail="Token ausente")
    
    try:
        # 1. Obtém o ID numérico do colaborador
        ctx = obter_dados_token(authorization)
        id_logado = int(ctx['id_colaborador'])

        # 2. Tratamento da Data/Hora para validação de conflitos (Mantém em formato local)
        try:
            dt_repo_inicio = datetime.strptime(dados.data_hora, "%Y-%m-%dT%H:%M")
        except ValueError:
            dt_repo_inicio = datetime.fromisoformat(dados.data_hora.replace('Z', ''))
            
        dt_repo_fim = dt_repo_inicio + timedelta(hours=1) 

        # 3. Verificação de Conflitos
        resp_turmas = supabase.table("tb_turmas").select("*").eq("id_professor", dados.id_professor).in_("status", ["Em Andamento", "Planejada"]).execute()
        for turma in resp_turmas.data:
            if not turma.get('data_inicio') or not turma.get('horario'): continue
            dt_inicio_turma = datetime.strptime(turma['data_inicio'], "%Y-%m-%d")
            dia_alvo = DIAS_MAPA.get(turma['dia_semana'].split("-")[0].strip(), 0)
            dias_diff = (dia_alvo - dt_inicio_turma.weekday() + 7) % 7
            dt_aula_atual = dt_inicio_turma + timedelta(days=dias_diff)
            
            try:
                hora_h, hora_m = map(int, turma['horario'].split("-")[0].strip().split(":"))
            except: continue

            for _ in range(turma.get('qtd_aulas', 1)):
                inicio_aula = dt_aula_atual.replace(hour=hora_h, minute=hora_m)
                fim_aula = inicio_aula + timedelta(hours=2, minutes=30)
                if (dt_repo_inicio < fim_aula) and (dt_repo_fim > inicio_aula):
                    raise HTTPException(status_code=409, detail=f"Conflito: Professor em aula na turma {turma['codigo_turma']}.")
                dt_aula_atual += timedelta(days=7)

        # =========================================================
        # 🌟 CORREÇÃO DO FUSO HORÁRIO (TIMEZONE BRASIL -03:00)
        # =========================================================
        data_salvar = dados.data_hora
        # Se vier no formato "YYYY-MM-DDTHH:MM" (16 caracteres), anexamos os segundos e o fuso
        if len(data_salvar) == 16: 
            data_salvar += ":00-03:00"
            
        # 4. Inserção no Banco de Dados
        payload = {
            "id_aluno": int(dados.id_aluno),
            "data_reposicao": data_salvar, # Agora vai com o fuso do Brasil!
            "codigo_turma": dados.turma_codigo,
            "id_professor": int(dados.id_professor),
            "conteudo_aula": dados.conteudo_aula or "Reposição",
            "motivo": dados.motivo or "Agendada via painel",
            "observacoes": dados.observacoes or "",
            "criado_por": id_logado,
            "status": "Agendada"
        }

        res = supabase.table("tb_reposicoes").insert(payload).execute()
        return {"message": "Agendada!", "data": res.data}

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Erro no agendamento: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Erro interno: {str(e)}")
@router.get("/agenda-geral")
def admin_agenda(authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)
    try:
        # Buscamos TUDO. Fazemos o join com alunos e professores
        resp_repo = supabase.table("tb_reposicoes")\
            .select("*, tb_alunos!left(nome_completo), professor:tb_colaboradores!tb_reposicoes_id_professor_fkey!left(nome_completo)")\
            .execute()

        eventos = []
        if resp_repo.data:
            for rep in resp_repo.data:
                nome_aluno = rep.get("tb_alunos", {}).get("nome_completo") if rep.get("tb_alunos") else "Aluno Desconhecido"
                
                # Se o join falhar, usamos o backup 'professor_nome_kurzy' da planilha
                nome_prof = rep.get("professor_nome_kurzy") or "Sem Professor"
                if rep.get("professor"):
                    nome_prof = rep["professor"].get("nome_completo") or nome_prof

                cor = "#28a745" if rep.get("status") == "Concluída" else "#ff4d4d"

                eventos.append({
                    "id": rep["id"], 
                    "title": f"{nome_aluno}", 
                    "start": rep["data_reposicao"],
                    "color": cor, 
                    "tipo": "reposicao",
                    "nome_aluno": nome_aluno,
                    "nome_prof": nome_prof,
                    "codigo_turma": rep.get("codigo_turma"),
                    "conteudo": rep.get("conteudo_aula"), 
                    "status": rep.get("status", "Agendada"),
                    "extendedProps": { "status": rep.get("status", "Agendada") }
                })
        return eventos
    except Exception as e:
        print(f"Erro na Agenda: {e}")
        return []
        
@router.put("/reposicao-completa/{id_repo}")
async def atualizar_reposicao_completa(
    id_repo: str, 
    background_tasks: BackgroundTasks, 
    data_hora: str = Form(None), # Novo: permite editar data
    id_professor: int = Form(None), # Novo: permite trocar professor
    conteudo_aula: str = Form(None), # Novo: permite editar conteúdo
    presenca: str = Form(None), 
    observacoes: str = Form(None), 
    arquivo: UploadFile = File(None), 
    authorization: str = Header(None)
):
    if not authorization: raise HTTPException(status_code=401)
    
    try:
        pres_bool = True if presenca == "true" else (False if presenca == "false" else None)
        updates = { "presenca": pres_bool, "observacoes": observacoes }

        if arquivo:
            file_content = await arquivo.read()
            file_ext = arquivo.filename.split('.')[-1]
            # Organização por subpasta dentro do bucket 'listas-chamada'
            file_path = f"reposicoes/assinatura_{id_repo}.{file_ext}" 
            
            # Upload rápido para o Supabase
            supabase.storage.from_("listas-chamada").upload(file_path, file_content, file_options={"content-type": arquivo.content_type, "upsert": "true"})
            updates["arquivo_assinatura"] = supabase.storage.from_("listas-chamada").get_public_url(file_path)

            # Dispara para o Google Drive em background
            background_tasks.add_task(enviar_reposicao_google_drive, id_repo, file_content, file_ext)

        supabase.table("tb_reposicoes").update(updates).eq("id", id_repo).execute()
        return {"message": "Reposição atualizada com sucesso!"}
        
    except Exception as e: 
        raise HTTPException(status_code=500, detail=str(e))

def enviar_reposicao_google_drive(id_repo: str, file_content: bytes, file_ext: str):
    try:
        print(f"[Drive-Repo] 1. Iniciando processo para a Reposição ID: {id_repo}")
        
        # Busca dados da reposição (incluindo o ID do professor responsável)
        repo_resp = supabase.table("tb_reposicoes").select("codigo_turma, data_reposicao, id_professor, id_aluno").eq("id", id_repo).single().execute()
        if not repo_resp.data: return
            
        dados = repo_resp.data
        data_aula = dados["data_reposicao"].split("T")[0]

        # Busca nomes para organização do arquivo
        aluno_nome = supabase.table("tb_alunos").select("nome_completo").eq("id_aluno", dados["id_aluno"]).single().execute().data.get("nome_completo", "ALUNO").upper()
        prof_nome = supabase.table("tb_colaboradores").select("nome_completo").eq("id_colaborador", dados["id_professor"]).single().execute().data.get("nome_completo", "").upper()

        # Dicionário de Pastas (Mesmo das chamadas)
        PASTAS_DOS_PROFESSORES = {
            "BRENO": '1PONtYJQnm0iQ1N9xYRIuYbYHueHb5y6a',
            "FELIPE": '1y9ar0CeQ0Nunw5B-ShOlDW6N66xy167k'
        }

        root_id = next((v for k, v in PASTAS_DOS_PROFESSORES.items() if k in prof_nome), None)
        if not root_id:
            print(f"[Drive-Repo] ❌ Sem pasta configurada para o professor: {prof_nome}")
            return

        # Setup do Token Google
        token_res = requests.post("https://oauth2.googleapis.com/token", data={
            "client_id": os.getenv("GDRIVE_CLIENT_ID"),
            "client_secret": os.getenv("GDRIVE_CLIENT_SECRET"),
            "refresh_token": os.getenv("GDRIVE_REFRESH_TOKEN"),
            "grant_type": "refresh_token"
        }).json()
        headers = {"Authorization": f"Bearer {token_res.get('access_token')}"}

        # Funções auxiliares de navegação
        def gerenciar_pasta(nome, parent_id):
            q = f"name='{nome}' and '{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
            res = requests.get("https://www.googleapis.com/drive/v3/files", headers=headers, params={"q": q}).json()
            if res.get("files"): return res["files"][0]["id"]
            return requests.post("https://www.googleapis.com/drive/v3/files", headers=headers, json={"name": nome, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]}).json().get("id")

        # Navega: Ano -> Mês -> REPOSIÇÃO
        dt = datetime.strptime(data_aula, "%Y-%m-%d")
        meses = {1: "JANEIRO", 2: "FEVEREIRO", 3: "MARÇO", 4: "ABRIL", 5: "MAIO", 6: "JUNHO", 7: "JULHO", 8: "AGOSTO", 9: "SETEMBRO", 10: "OUTUBRO", 11: "NOVEMBRO", 12: "DEZEMBRO"}
        
        ano_id = gerenciar_pasta(dt.strftime("%Y"), root_id)
        mes_nome = f"{dt.month:02d} {meses[dt.month]}" if "BRENO" in prof_nome else meses[dt.month]
        mes_id = gerenciar_pasta(mes_nome, ano_id)
        repo_folder_id = gerenciar_pasta("REPOSIÇÃO", mes_id)

        # Upload final
        nome_arquivo = f"{dt.strftime('%d-%m-%Y')} REPOSICAO - {aluno_nome} - TURMA {dados['codigo_turma']}.{file_ext}"
        metadata = {"name": nome_arquivo, "parents": [repo_folder_id]}
        requests.post("https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart", headers=headers, 
                      files={'metadata': (None, json.dumps(metadata), 'application/json'), 'file': (nome_arquivo, file_content)})
        
        print(f"[Drive-Repo] ✅ Sucesso: {nome_arquivo} enviado.")
    except Exception as e: print(f"[Drive-Repo] ❌ Erro: {e}")

@router.patch("/editar-reposicao/{id_repo}")
def atualizar_dados_reposicao(
    id_repo: str, 
    dados: dict, # Recebe um dicionário para ser flexível
    authorization: str = Header(None)
):
    if not authorization: raise HTTPException(status_code=401)
    
    # 1. Validação de quem está editando
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)
    
    # Apenas Coordenação (4), Gerente/Diretor (8) ou TI (8) podem editar
    if ctx['nivel'] < 4:
        raise HTTPException(status_code=403, detail="Sem permissão para editar reposições.")

    try:
        # 2. Filtramos apenas os campos que podem ser editados
        campos_permitidos = [
            "id_aluno", "id_professor", "codigo_turma", 
            "data_reposicao", "conteudo_aula", "motivo", "observacoes"
        ]
        
        updates = {k: v for k, v in dados.items() if k in campos_permitidos}

        if not updates:
            return {"message": "Nenhum campo válido para atualização enviado."}

        # 3. Executa o update no Supabase
        res = supabase.table("tb_reposicoes").update(updates).eq("id", id_repo).execute()
        
        if not res.data:
            raise HTTPException(status_code=404, detail="Reposição não encontrada.")

        return {"message": "Reposição editada com sucesso!", "dados": res.data[0]}

    except Exception as e:
        logger.error(f"Erro ao editar reposição: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/reposicao/finalizar")
async def finalizar_reposicao(id_reposicao: int, authorization: str = Header(None)):
    rep = supabase.table("tb_reposicoes").select("*").eq("id", id_reposicao).single().execute()
    if not rep.data: raise HTTPException(status_code=404)
    dados = rep.data
    supabase.table("tb_reposicoes").update({"status": "Concluída"}).eq("id", id_reposicao).execute()
    supabase.table("tb_chamadas").update({"status_presenca": "R"}).eq("id_aluno", dados['id_aluno']).eq("data_aula", dados['data_falta']).execute()
    return {"message": "Concluída e convertida (R)"}

@router.post("/reposicao/concluir-e-converter")
async def concluir_reposicao(id_repo: str, authorization: str = Header(None)):
    logger.info(f"--- CONVERTENDO REPOSIÇÃO PARA PRESENÇA (R) ---")
    logger.info(f"ID Reposição: {id_repo}")
    
    ctx = obter_dados_token(authorization)
    try:
        resp = supabase.table("tb_reposicoes").select("*").eq("id", id_repo).execute()
        
        if not resp.data:
            logger.warning(f"⚠️ Reposição {id_repo} não encontrada no banco.")
            raise HTTPException(status_code=404, detail="Reposição não encontrada")
            
        repo = resp.data[0]
        logger.info(f"Reposição encontrada para Aluno ID: {repo['id_aluno']}")
        
        # Atualiza status
        supabase.table("tb_reposicoes").update({"status": "Concluída"}).eq("id", id_repo).execute()
        logger.info(f"Status da reposição atualizado para 'Concluída'.")
        
        if repo.get('data_falta_original'):
            logger.info(f"Convertendo falta do dia {repo['data_falta_original']} em 'R'...")
            supabase.table("tb_chamadas").update({"status_presenca": "R"})\
                .eq("id_aluno", repo['id_aluno'])\
                .eq("data_aula", repo['data_falta_original']).execute()
            logger.info(f"✅ Falta convertida com sucesso.")
                
        return {"status": "success", "message": "Sucesso"}
        
    except Exception as e:
        logger.error(f"❌ ERRO NA CONVERSÃO: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# =========================================
# CRM / LEADS E FESTAS
# =========================================

@router.get("/leads-crm")
def get_leads_crm(filtro_unidade: int | None = None, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)
    try:
        query = supabase.table("inscricoes").select("*").order("created_at", desc=True)
        if ctx['nivel'] < 9: query = query.eq("id_unidade", ctx['id_unidade'])
        elif filtro_unidade: query = query.eq("id_unidade", filtro_unidade)

        leads = query.execute().data
        alunos = supabase.table("tb_alunos").select("cpf").execute().data
        cpfs = set(''.join(filter(str.isdigit, a['cpf'])) for a in alunos if a.get('cpf'))
        
        res = []
        for l in leads:
            cpf_l = ''.join(filter(str.isdigit, l.get('cpf','') or ''))
            res.append({
                "id": l['id'], "nome": l['nome'], "cpf": l.get('cpf','-'), "whatsapp": l['whatsapp'],
                "workshop": l['workshop'], "data_agendada": l['data_agendada'], "status": l.get('status','Pendente'),
                "vendedor": l.get('vendedor','-'), "ja_e_aluno": (cpf_l in cpfs and cpf_l != ''), "id_unidade": l.get('id_unidade') 
            })
        return res
    except: return []

@router.patch("/leads-crm/{id_inscricao}")
def atualizar_status_lead(id_inscricao: int, dados: StatusUpdateData, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)
    if ctx['nivel'] not in [3, 4, 8, 9, 10]: raise HTTPException(status_code=403)
    try:
        resp = supabase.table("tb_colaboradores").select("nome_completo").eq("user_id", ctx['user_id']).execute()
        nome = resp.data[0]['nome_completo']
        supabase.table("inscricoes").update({ "status": dados.status, "vendedor": nome }).eq("id", id_inscricao).execute()
        return {"message": "OK"}
    except: raise HTTPException(status_code=500)

@router.get("/festas-aniversario")
def listar_festas_aniversario(status: Optional[str] = None, q: Optional[str] = None, data_ini: Optional[str] = None, data_fim: Optional[str] = None, id_vendedor: Optional[int] = None, id_unidade: Optional[int] = None, sort_by: str = "data_festa", sort_dir: str = "asc", authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)
    if ctx["nivel"] not in (8, 9, 10): raise HTTPException(status_code=403)
    try:
        query = supabase.table("tb_festas_aniversario").select("*, tb_unidades(nome_unidade), tb_colaboradores(nome_completo)")
        if status: query = query.eq("status", status)
        if data_ini: query = query.gte("data_festa", data_ini)
        if data_fim: query = query.lte("data_festa", data_fim)
        if id_vendedor: query = query.eq("id_vendedor", id_vendedor)
        if ctx["nivel"] == 8: query = query.eq("id_unidade", ctx["id_unidade"])
        elif id_unidade: query = query.eq("id_unidade", id_unidade)
        if q: query = query.or_(f"contratante.ilike.%{q}%,aniversariante.ilike.%{q}%,telefone.ilike.%{q}%")
        desc = (sort_dir or "").lower() == "desc"
        return query.order(sort_by, desc=desc).execute().data
    except:
        try: return supabase.table("tb_festas_aniversario").select("*").execute().data
        except: return []

@router.get("/festas-aniversario/vendedores")
def listar_vendedores_festas(authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)
    if ctx["nivel"] not in (8, 9, 10): raise HTTPException(status_code=403)
    try:
        q = supabase.table("tb_colaboradores").select("id_colaborador, nome_completo").eq("ativo", True)
        if ctx["nivel"] == 8: q = q.eq("id_unidade", ctx["id_unidade"])
        return q.order("nome_completo").execute().data
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@router.post("/festas-aniversario")
def criar_festa_aniversario(dados: FestaAniversarioCreate, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)
    if ctx["nivel"] not in (8, 9, 10): raise HTTPException(status_code=403)
    payload = dados.model_dump(exclude_none=True)
    if ctx["nivel"] == 8: payload["id_unidade"] = ctx["id_unidade"]
    else: payload["id_unidade"] = payload.get("id_unidade") or 1
    payload["tipo"] = "ANIVERSARIO_GAMER"
    try:
        resp = supabase.table("tb_festas_aniversario").insert(payload).execute()
        return resp.data[0] if resp.data else {"message": "ok"}
    except Exception as e: raise HTTPException(status_code=400, detail=str(e))

@router.put("/festas-aniversario/{id_festa}")
def editar_festa_aniversario(id_festa: int, dados: FestaAniversarioUpdate, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)
    if ctx["nivel"] not in (8, 9, 10): raise HTTPException(status_code=403)
    updates = dados.model_dump(exclude_none=True)
    try:
        if ctx["nivel"] == 8:
            festa = supabase.table("tb_festas_aniversario").select("id_unidade").eq("id", id_festa).single().execute()
            if not festa.data: raise HTTPException(status_code=404)
            if festa.data.get("id_unidade") != ctx["id_unidade"]: raise HTTPException(status_code=403)
            updates.pop("id_unidade", None)
        supabase.table("tb_festas_aniversario").update(updates).eq("id", id_festa).execute()
        return {"message": "Festa atualizada!"}
    except Exception as e: raise HTTPException(status_code=400, detail=str(e))


# =========================================
# CHAT E MENSAGENS
# =========================================

@router.get("/chat/conversas-ativas")
def admin_listar_conversas_ativas(authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    try:
        token = authorization.split(" ")[1]
        ctx = get_contexto_usuario(token)
        lista_alunos_permitidos = []
        filtrar_por_aluno = False
        
        if ctx['nivel'] == 5:
            turmas_resp = supabase.table("tb_turmas").select("codigo_turma").eq("id_professor", ctx['id_colaborador']).execute()
            codigos_turmas = [t['codigo_turma'] for t in turmas_resp.data]
            if not codigos_turmas: return []
            matriculas_resp = supabase.table("tb_matriculas").select("id_aluno").in_("codigo_turma", codigos_turmas).execute()
            lista_alunos_permitidos = [m['id_aluno'] for m in matriculas_resp.data]
            filtrar_por_aluno = True
        elif ctx['nivel'] < 9:
            alunos_unidade = supabase.table("tb_alunos").select("id_aluno").eq("id_unidade", ctx['id_unidade']).execute()
            lista_alunos_permitidos = [a['id_aluno'] for a in alunos_unidade.data]
            filtrar_por_aluno = True

        query = supabase.table("tb_chat").select("id_aluno, mensagem, created_at, tb_alunos(nome_completo)").order("created_at", desc=True).limit(300)
        if filtrar_por_aluno:
            if not lista_alunos_permitidos: return []
            query = query.in_("id_aluno", lista_alunos_permitidos)
            
        msgs = query.execute()
        conversas = {}
        for m in msgs.data:
            aid = m['id_aluno']
            if aid not in conversas:
                conversas[aid] = {"id_aluno": aid, "nome": m['tb_alunos']['nome_completo'], "ultima_msg": m['mensagem'], "data": m['created_at']}
        return list(conversas.values())
    except: return []

@router.get("/chat/mensagens/{id_aluno}")
def admin_ler_mensagens(id_aluno: int, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    try:
        token = authorization.split(" ")[1]
        ctx = get_contexto_usuario(token)
        permitido = True

        if ctx['nivel'] == 5:
            turmas_resp = supabase.table("tb_turmas").select("codigo_turma").eq("id_professor", ctx['id_colaborador']).execute()
            codigos = [t['codigo_turma'] for t in turmas_resp.data]
            if not codigos: permitido = False
            else:
                matr_resp = supabase.table("tb_matriculas").select("id_aluno").in_("codigo_turma", codigos).execute()
                alunos = [m['id_aluno'] for m in matr_resp.data]
                permitido = id_aluno in alunos
        elif ctx['nivel'] < 9:
            alunos_unidade = supabase.table("tb_alunos").select("id_aluno").eq("id_unidade", ctx['id_unidade']).execute()
            alunos = [a['id_aluno'] for a in alunos_unidade.data]
            permitido = id_aluno in alunos

        if not permitido: raise HTTPException(status_code=403, detail="Sem permissão.")
        msgs = supabase.table("tb_chat").select("*").eq("id_aluno", id_aluno).order("created_at").execute()
        return msgs.data
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@router.post("/chat/responder")
def admin_responder(dados: ChatAdminReply, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    try:
        token = authorization.split(" ")[1]
        ctx = get_contexto_usuario(token)
        id_colab_save = ctx['id_colaborador'] if ctx['nivel'] >= 4 else None
        supabase.table("tb_chat").insert({
            "id_aluno": dados.id_aluno, "mensagem": dados.mensagem, "enviado_por_admin": True, "id_colaborador": id_colab_save
        }).execute()
        return {"message": "Respondido"}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@router.get("/chat/historico-unificado")
def get_historico_unificado(authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    try:
        chats_privados = supabase.table("tb_chat").select("*, tb_alunos(nome_completo)").order("created_at", desc=True).limit(200).execute()
        chats_grupos = supabase.table("tb_chat_turma").select("*").order("created_at", desc=True).limit(200).execute()
            
        historico = []
        ids_processados, grupos_processados = set(), set()

        for c in chats_privados.data:
            id_aluno = c['id_aluno']
            if id_aluno not in ids_processados:
                nome = c['tb_alunos']['nome_completo'] if c.get('tb_alunos') else "Aluno Desconhecido"
                historico.append({"tipo": "privado", "id": id_aluno, "nome": nome, "ultima_msg": c['mensagem'], "timestamp": c['created_at'], "lida": c['lida']})
                ids_processados.add(id_aluno)

        for g in chats_grupos.data:
            cod_turma = g['codigo_turma']
            if cod_turma not in grupos_processados:
                historico.append({"tipo": "grupo", "id": cod_turma, "nome": f"Grupo {cod_turma}", "ultima_msg": f"{g['nome_exibicao']}: {g['mensagem']}", "timestamp": g['created_at'], "lida": True})
                grupos_processados.add(cod_turma)

        historico.sort(key=lambda x: x['timestamp'], reverse=True)
        return historico
    except: return []

@router.get("/chat/mensagens-com/{target}")
def get_mensagens_chat(target: str, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    try:
        token = authorization.split(" ")[1]
        user_id = supabase.auth.get_user(token).user.id
        aluno = supabase.table("tb_alunos").select("id_aluno").eq("user_id", user_id).single().execute()
        id_aluno = aluno.data['id_aluno']

        query = supabase.table("tb_chat").select("*").eq("id_aluno", id_aluno)
        if target == 'geral': query = query.is_("id_colaborador", "null")
        else: query = query.eq("id_colaborador", int(target))
        return query.order("created_at").execute().data
    except: return []

@router.post("/chat/enviar-direto")
def enviar_mensagem_aluno(dados: dict, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    try:
        token = authorization.split(" ")[1]
        user_id = supabase.auth.get_user(token).user.id
        aluno = supabase.table("tb_alunos").select("id_aluno").eq("user_id", user_id).single().execute()
        supabase.table("tb_chat").insert({
            "id_aluno": aluno.data['id_aluno'], "mensagem": dados['mensagem'], "id_colaborador": dados.get('id_colaborador'), "enviado_por_admin": False
        }).execute()
        return {"status": "ok"}
    except: raise HTTPException(status_code=400)

@router.get("/chat/mensagens-grupo/{codigo_turma}")
def get_mensagens_grupo(codigo_turma: str, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    try:
        return supabase.table("tb_chat").select("*").eq("codigo_turma", codigo_turma).order("created_at").execute().data
    except: return []

@router.get("/chat/turma/{codigo_turma}")
def get_chat_turma(codigo_turma: str, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    try:
        return supabase.table("tb_chat_turma").select("*").eq("codigo_turma", codigo_turma).order("created_at", desc=False).limit(100).execute().data
    except: return []

@router.post("/chat/turma/enviar")
def enviar_chat_turma(dados: MensagemGrupoData, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    try:
        token = authorization.split(" ")[1]
        user_id = supabase.auth.get_user(token).user.id
        nome_exibicao, cargo_exibicao = "Usuário", "Aluno"
        
        colab_resp = supabase.table("tb_colaboradores").select("nome_completo, id_cargo").eq("user_id", user_id).execute()
        if colab_resp and hasattr(colab_resp, 'data') and colab_resp.data:
            c = colab_resp.data[0]
            nome_exibicao = c['nome_completo'].split()[0]
            cargo_exibicao = "Professor" if c['id_cargo'] == 6 else "Staff"
        else:
            aluno_resp = supabase.table("tb_alunos").select("nome_completo").eq("user_id", user_id).execute()
            if aluno_resp and hasattr(aluno_resp, 'data') and aluno_resp.data:
                nome_exibicao = aluno_resp.data[0]['nome_completo'].split()[0]

        supabase.table("tb_chat_turma").insert({
            "codigo_turma": dados.codigo_turma, "mensagem": dados.mensagem, "id_usuario_envio": user_id, "nome_exibicao": nome_exibicao, "cargo_exibicao": cargo_exibicao
        }).execute()
        return {"message": "OK"}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@router.get("/aluno/meus-contatos")
def get_contatos_aluno(authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    try:
        token = authorization.split(" ")[1]
        user_id = supabase.auth.get_user(token).user.id

        aluno_resp = supabase.table("tb_alunos").select("id_aluno, id_unidade").eq("user_id", user_id).single().execute()
        if not aluno_resp.data: return []
        id_unidade, id_aluno = aluno_resp.data['id_unidade'], aluno_resp.data['id_aluno']
        contatos = []

        coords = supabase.table("tb_colaboradores").select("id_colaborador, nome_completo").eq("id_unidade", id_unidade).eq("id_cargo", 4).eq("ativo", True).execute()
        for c in coords.data:
            contatos.append({"id": c['id_colaborador'], "nome": c['nome_completo'], "cargo": "Coordenador Pedagógico", "tipo": "Coordenacao", "codigo_turma_grupo": None})

        matricula = supabase.table("tb_matriculas").select("codigo_turma").eq("id_aluno", id_aluno).execute()
        if matricula.data:
            cod_turma = matricula.data[0]['codigo_turma']
            turma_info = supabase.table("tb_turmas").select("nome_curso, id_professor, tb_colaboradores(nome_completo)").eq("codigo_turma", cod_turma).single().execute()
            if turma_info.data:
                t = turma_info.data
                contatos.append({"id": f"grupo-{cod_turma}", "nome": f"Grupo {t['nome_curso']}", "cargo": f"Turma {cod_turma}", "tipo": "Grupo", "codigo_turma_grupo": cod_turma})
                if t.get('tb_colaboradores'):
                    contatos.append({"id": t['id_professor'], "nome": t['tb_colaboradores']['nome_completo'], "cargo": f"Prof. {t['nome_curso']}", "tipo": "Professor", "codigo_turma_grupo": None})

        contatos.append({"id": "geral", "nome": "Suporte Javis", "cargo": "Secretaria", "tipo": "Admin", "codigo_turma_grupo": None})
        return contatos
    except: return []


# =========================================
# DASHBOARD E FREQUÊNCIAS GERAIS
# =========================================

@router.get("/dashboard-stats")
def get_dashboard_stats(authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)
    
    try:
        # 1. TOTAL DE ALUNOS (Busca real da tabela de alunos)
        q_alunos = supabase.table("tb_alunos").select("id_aluno", count="exact")
        if ctx['nivel'] < 9: 
            q_alunos = q_alunos.eq("id_unidade", ctx['id_unidade'])
        total_alunos = q_alunos.execute().count or 0

        # 2. TURMAS ATIVAS (Conforme sua legenda)
        # Ativas = Em Andamento + Fechada
        q_turmas = supabase.table("tb_turmas").select("status")
        if ctx['nivel'] < 9: 
            q_turmas = q_turmas.eq("id_unidade", ctx['id_unidade'])
        turmas_data = q_turmas.execute().data or []
        
        # Filtro rigoroso com os nomes que você usa no banco
        status_ativos = ["Em Andamento", "Fechada"]
        turmas_ativas = sum(1 for t in turmas_data if t.get('status') in status_ativos)

        # 3. SALDO DE REPOSIÇÕES (Dívida Pedagógica 2026)
        # Saldo = Faltas - Reposições Realizadas
        hoje = datetime.now()
        inicio_ano = f"{hoje.year}-01-01"
        
        q_freq = supabase.table("tb_frequencia_eventos")\
            .select("status, quantidade_aulas")\
            .gte("data_aula", inicio_ano)
        
        freq_data = q_freq.execute().data or []

        total_faltas = 0
        total_repostas = 0

        for item in freq_data:
            status = item.get('status')
            qtd = item.get('quantidade_aulas') or 1
            
            if status == 'F':
                total_faltas += 1 # Cada falta (F) gera 1 aula de débito
            elif status == 'R':
                total_repostas += qtd # Cada reposição (R) abate o peso das aulas

        # Saldo pendente (não deixa ficar negativo)
        reposicoes_pendentes = max(0, total_faltas - total_repostas)

        # 4. LEADS E CONVERSÃO (Para o card de Marketing)
        q_leads = supabase.table("inscricoes").select("status")
        if ctx['nivel'] < 9: 
            q_leads = q_leads.eq("id_unidade", ctx['id_unidade'])
        leads_data = q_leads.execute().data or []
        
        total_leads = len(leads_data)
        matriculados = sum(1 for l in leads_data if l.get('status') == 'Matriculado')
        taxa_conversao = (matriculados / total_leads * 100) if total_leads > 0 else 0

        return {
            "leads": { 
                "total": total_leads, 
                "conversao": round(taxa_conversao, 1) 
            },
            "escola": { 
                "total_alunos": total_alunos, # REATIVADO: Contagem real de alunos
                "turmas_ativas": turmas_ativas 
            },
            "reposicoes": reposicoes_pendentes, # Card de Débito de Aulas
            "grafico_cursos": {} 
        }
    except Exception as e:
        logger.error(f"Erro ao carregar Dashboard Stats: {e}")
        return {
            "leads": {"total": 0, "conversao": 0},
            "escola": {"total_alunos": 0, "turmas_ativas": 0},
            "reposicoes": 0
        }

@router.get("/relatorio-frequencia-geral")
def listar_frequencia_geral(q: Optional[str] = None, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)
    
    try:
        # Lemos DIRETO da tabela de chamadas, fazendo JOIN com tb_alunos para pegar o nome
        query = supabase.table("tb_chamadas").select("id_aluno, codigo_turma, data_aula, status_presenca, tb_alunos(nome_completo)")
        
        # Filtra pelo código da turma (que o JS envia via parâmetro 'q')
        if q: 
            query = query.eq("codigo_turma", q.strip())
            
        # Ordena pela data da aula para garantir a linha do tempo correta (Aula 1, Aula 2...)
        dados = query.order("data_aula", desc=False).execute().data
        
        eventos_processados = []
        contador_aulas_por_aluno = {}
        
        for item in dados:
            aluno_id = item['id_aluno']
            
            # Extrai o nome do aluno de forma segura
            nome_aluno = "Desconhecido"
            if item.get('tb_alunos') and isinstance(item['tb_alunos'], dict):
                nome_aluno = item['tb_alunos'].get('nome_completo', 'Desconhecido')
            
            # Inicia o contador de aulas para este aluno, se for a primeira vez
            if aluno_id not in contador_aulas_por_aluno:
                contador_aulas_por_aluno[aluno_id] = 1
                
            eventos_processados.append({
                "id_aluno": aluno_id,
                "nome": nome_aluno,
                "turma": item.get('codigo_turma'),
                "status": item.get('status_presenca'), # Aqui vem o 'P', 'F' ou 'R'
                "numero_aula": contador_aulas_por_aluno[aluno_id]
            })
            
            # Incrementa a aula para a próxima passagem
            contador_aulas_por_aluno[aluno_id] += 1
            
        return eventos_processados
        
    except Exception as e: 
        logger.error(f"Erro no relatório de frequência direto: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/dashboard-frequencia-unificado")
def stats_frequencia_unificado(data_inicio: str = None, data_fim: str = None, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    
    try:
        # Busca dados na VIEW (Certifique-se que a coluna quantidade_aulas está na VIEW vw_frequencia_dashboard)
        query = supabase.table("vw_frequencia_dashboard").select("*").not_.is_("nome_professor_atual", "null")
        
        if data_inicio: query = query.gte("data_aula", data_inicio)
        if data_fim: query = query.lte("data_aula", data_fim)
            
        dados_raw = query.execute().data or []
        
        # Estrutura inicial do Dashboard
        stats = {
            "global": {"presencas": 0, "faltas": 0, "reposicoes": 0, "assiduidade": 0}, 
            "por_curso": {}, "por_turma": {}, "por_mes": {}, "por_professor": {}, "alunos_criticos": {}
        }

        for item in dados_raw:
            status = item.get('status')
            nome_prof = item.get('nome_professor_atual') or 'Sem Professor'
            curso = item.get('curso') or 'Não Definido'
            turma = item.get('codigo_turma') or 'Sem Turma'
            mes = item.get('data_aula', '0000-00')[:7] if item.get('data_aula') else 'Sem Data'
            nome_aluno = item.get('nome_aluno')

            # --- LÓGICA DE SOMA PELO PESO DA AULA ---
            # Se for Reposição, somamos a quantidade de aulas dadas.
            # Se for Presença normal ou Falta, cada registro vale 1 aula.
            if status == 'R':
                peso_aula = item.get('quantidade_aulas') or 3 # Padrão 3 se vier nulo do banco
            else:
                peso_aula = 1

            # Atualização dos Totais Globais
            if status == 'P': 
                stats["global"]["presencas"] += peso_aula
            elif status == 'F': 
                stats["global"]["faltas"] += peso_aula
            elif status == 'R': 
                stats["global"]["reposicoes"] += peso_aula

            # Atualização dos Agrupamentos (Gráficos por Professor, Turma, etc.)
            for cat, chave in [("por_curso", curso), ("por_turma", turma), ("por_mes", mes), ("por_professor", nome_prof)]:
                if chave not in stats[cat]: 
                    stats[cat][chave] = {"P": 0, "F": 0, "R": 0}
                
                if status in ["P", "F", "R"]: 
                    stats[cat][chave][status] += peso_aula

            # Ranking de Alunos Críticos (Soma o peso das faltas)
            if status == 'F' and nome_aluno:
                if nome_aluno not in stats["alunos_criticos"]: 
                    stats["alunos_criticos"][nome_aluno] = {"faltas": 0, "turma": turma}
                stats["alunos_criticos"][nome_aluno]["faltas"] += peso_aula

        # Cálculo da Assiduidade (P / P+F) - Mantemos apenas aulas normais nesta métrica pedagógica
        total_aulas_normais = stats["global"]["presencas"] + stats["global"]["faltas"]
        if total_aulas_normais > 0: 
            stats["global"]["assiduidade"] = round((stats["global"]["presencas"] / total_aulas_normais * 100), 1)

        # Formatação final da lista de alunos críticos
        lista_criticos = [{"nome": k, "faltas": v["faltas"], "turma": v["turma"]} for k, v in stats["alunos_criticos"].items()]
        stats["alunos_criticos"] = sorted(lista_criticos, key=lambda x: x['faltas'], reverse=True)[:10]

        return stats
    except Exception as e: 
        logger.error(f"Erro no dashboard: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# =========================================
# CHAMADAS
# =========================================

@router.get("/chamada/turma/{codigo_turma}")
def listar_alunos_chamada(codigo_turma: str, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    try:
        return supabase.table("tb_matriculas").select("id_aluno, tb_alunos(nome_completo)").eq("codigo_turma", codigo_turma).execute().data
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@router.post("/chamada/salvar")
def salvar_chamada(dados: list, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)
    try:
        for item in dados:
            item['id_professor'] = ctx['id_colaborador']
            item['data_aula'] = datetime.now().strftime("%Y-%m-%d")
        supabase.table("tb_chamadas").upsert(dados, on_conflict="id_aluno,codigo_turma,data_aula").execute()
        return {"message": "Sucesso!"}
    except Exception as e: raise HTTPException(status_code=400, detail=str(e))

@router.post("/chamada/salvar-v2")
def salvar_chamada_estruturada(lista: List[ItemChamada], authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token_str = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token_str)
    id_prof = ctx.get("id_colaborador")

    try:
        dados_chamada = []
        for item in lista:
            dados_chamada.append({
                "id_aluno": item.id_aluno, "codigo_turma": item.codigo_turma, "data_aula": item.data_aula,
                "id_professor": id_prof, "presenca": item.presenca, "created_at": datetime.now().isoformat()
            })
        supabase.table("tb_chamadas").insert(dados_chamada).execute()
        return {"status": "success", "count": len(dados_chamada)}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

# --- FUNÇÃO ASSÍNCRONA PARA O GOOGLE DRIVE COM RASTREADORES ---
def enviar_para_google_drive(codigo_turma: str, data_aula: str, file_content: bytes, file_ext: str, id_prof_logado: int):
    try:
        print(f"[Drive] 1. Iniciando upload em background para a turma {codigo_turma}...")
        
        # 1. Buscando o VERDADEIRO dono da turma (Professor da Turma)
        print("[Drive] 2. Buscando quem é o professor dono desta turma...")
        turma_resp = supabase.table("tb_turmas").select("nome_curso, id_professor").eq("codigo_turma", codigo_turma).single().execute()
        
        nome_curso = turma_resp.data.get("nome_curso", "") if turma_resp.data else ""
        id_verdadeiro_prof = turma_resp.data.get("id_professor") if turma_resp.data else id_prof_logado

        if not id_verdadeiro_prof:
            print("[Drive] ❌ ERRO: Não foi possível identificar o professor da turma!")
            return

        # 2. Buscando o nome do professor da turma
        prof_resp = supabase.table("tb_colaboradores").select("nome_completo").eq("id_colaborador", id_verdadeiro_prof).single().execute()
        nome_prof = prof_resp.data.get("nome_completo", "").upper() if prof_resp.data else ""
        print(f"[Drive] 3. Professor dono da turma encontrado: {nome_prof}")

        # ==========================================================
        # 🌟 DICIONÁRIO DE PASTAS DOS PROFESSORES NO GOOGLE DRIVE
        # Adicione novos professores aqui quando precisar!
        # ==========================================================
        PASTAS_DOS_PROFESSORES = {
            "BRENO": '1PONtYJQnm0iQ1N9xYRIuYbYHueHb5y6a',
            "FELIPE": '1y9ar0CeQ0Nunw5B-ShOlDW6N66xy167k'
            # "JOAO": 'coloque_o_id_da_pasta_do_joao_aqui',
            # "MARCOS": 'coloque_o_id_da_pasta_aqui'
        }

        # 3. Descobrindo o ID da Pasta raiz baseado no nome do professor
        root_id = None
        for chave_nome, id_pasta in PASTAS_DOS_PROFESSORES.items():
            if chave_nome in nome_prof:
                root_id = id_pasta
                break

        if not root_id:
            print(f"[Drive] ❌ Cancelado: O professor '{nome_prof}' ainda não tem uma pasta configurada no Dicionário.")
            return

        print("[Drive] 4. Pasta raiz do professor encontrada. Pegando credenciais do Render...")
        client_id = os.getenv("GDRIVE_CLIENT_ID")
        client_secret = os.getenv("GDRIVE_CLIENT_SECRET")
        refresh_token = os.getenv("GDRIVE_REFRESH_TOKEN")

        if not client_id or not refresh_token:
            print("[Drive] ❌ ERRO: As variáveis de ambiente do GDrive não estão configuradas no Render!")
            return

        print("[Drive] 5. Gerando Token de Acesso do Google...")
        token_res = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token"
            }
        ).json()
        
        access_token = token_res.get("access_token")
        if not access_token:
            print(f"[Drive] ❌ Falha ao gerar Access Token: {token_res}")
            return
            
        print("[Drive] 6. Token gerado com sucesso. Navegando nas pastas...")
        headers = {"Authorization": f"Bearer {access_token}"}

        # Funções Auxiliares para o Drive
        def buscar_pasta(nome, parent_id):
            q = f"name='{nome}' and '{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
            res = requests.get("https://www.googleapis.com/drive/v3/files", headers=headers, params={"q": q}).json()
            return res.get("files", [])[0]["id"] if res.get("files") else None

        def criar_pasta(nome, parent_id):
            res = requests.post("https://www.googleapis.com/drive/v3/files", headers=headers, json={"name": nome, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]}).json()
            return res.get("id")

        dt_aula = datetime.strptime(data_aula, "%Y-%m-%d")
        ano_str = dt_aula.strftime("%Y")
        meses = {1: "JANEIRO", 2: "FEVEREIRO", 3: "MARÇO", 4: "ABRIL", 5: "MAIO", 6: "JUNHO", 7: "JULHO", 8: "AGOSTO", 9: "SETEMBRO", 10: "OUTUBRO", 11: "NOVEMBRO", 12: "DEZEMBRO"}
        mes_num = dt_aula.month
        mes_nome = meses[mes_num]
        
        nome_pasta_turma = codigo_turma

        if "BRENO" in nome_prof:
            mes_nome = f"{mes_num:02d} {mes_nome}"
            nome_pasta_turma = f"{codigo_turma} {nome_curso}".strip()

        ano_id = buscar_pasta(ano_str, root_id) or criar_pasta(ano_str, root_id)
        mes_id = buscar_pasta(mes_nome, ano_id) or criar_pasta(mes_nome, ano_id)
        turma_id = buscar_pasta(nome_pasta_turma, mes_id) or criar_pasta(nome_pasta_turma, mes_id)

        print(f"[Drive] 7. Pastas prontas (Turma ID: {turma_id}). Iniciando envio da foto...")

        nome_arquivo = f"{dt_aula.strftime('%d-%m-%Y')} TURMA - {codigo_turma}.{file_ext}"
        metadata = {"name": nome_arquivo, "parents": [turma_id]}
        
        files = {
            'metadata': (None, json.dumps(metadata), 'application/json'),
            'file': (nome_arquivo, file_content, f'image/{file_ext}')
        }
        
        res_upload = requests.post(
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
            headers=headers,
            files=files
        )
        
        if res_upload.status_code == 200:
            print(f"[Drive] 8. ✅ SUCESSO ABSOLUTO! Arquivo salvo na turma {codigo_turma}.")
        else:
            print(f"[Drive] ❌ Erro no upload final: {res_upload.text}")

    except Exception as e:
        print(f"[Drive] ❌ ERRO FATAL no background task: {str(e)}")
@router.post("/chamada/salvar-v3")
async def salvar_chamada_foto(
    background_tasks: BackgroundTasks,
    codigo_turma: str = Form(...), 
    data_aula: str = Form(...), 
    lista_alunos: str = Form(...),
    arquivo_foto: UploadFile = File(...), 
    authorization: str = Header(None)
):
    # LOG DE ENTRADA
    logger.info(f"--- INICIANDO SALVAMENTO DE CHAMADA V3 ---")
    logger.info(f"Turma: {codigo_turma} | Data: {data_aula}")
    
    try:
        ctx = obter_dados_token(authorization)
        id_prof = ctx.get("id_colaborador")
        lista_alunos_obj = json.loads(lista_alunos)
        
        logger.info(f"Professor logado ID: {id_prof} | Alunos enviados: {len(lista_alunos_obj)}")

        file_content = await arquivo_foto.read()
        file_ext = arquivo_foto.filename.split('.')[-1]
        file_path = f"chamadas/{codigo_turma}_{data_aula}.{file_ext}"
        
        # LOG DE STORAGE
        logger.info(f"Fazendo upload da foto para: {file_path}")
        supabase.storage.from_("listas-chamada").upload(file_path, file_content, file_options={"content-type": arquivo_foto.content_type, "upsert": "true"})
        foto_url = supabase.storage.from_("listas-chamada").get_public_url(file_path)

        dados_insercao = []
        for item in lista_alunos_obj:
            dados_insercao.append({
                "id_aluno": item["id_aluno"], "codigo_turma": codigo_turma, "data_aula": data_aula,
                "id_professor": id_prof, "status_presenca": item["status_presenca"], "url_assinatura": foto_url 
            })

        # LOG DE BANCO
        logger.info(f"Tentando inserir {len(dados_insercao)} registros na tb_chamadas...")
        res = supabase.table("tb_chamadas").upsert(dados_insercao, on_conflict="id_aluno,codigo_turma,data_aula").execute()
        
        logger.info(f"✅ Chamada salva no banco com sucesso.")

        background_tasks.add_task(enviar_para_google_drive, codigo_turma, data_aula, file_content, file_ext, id_prof)
        logger.info(f"Tarefa de background (Google Drive) agendada.")

        return {"status": "success", "url_foto": foto_url}
    
    except Exception as e:
        # LOG DE ERRO CRÍTICO
        logger.error(f"❌ ERRO CRÍTICO NA CHAMADA: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")


@router.get("/listar-professores")
def admin_listar_professores(authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)
    try:
        # Busca funcionários com cargo de Professor (6) ou Coordenador (4)
        query = supabase.table("tb_colaboradores").select("id_colaborador, nome_completo").in_("id_cargo", [6, 4])
        if ctx['nivel'] < 9: 
            query = query.eq("id_unidade", ctx['id_unidade'])
        return query.execute().data
    except: 
        return []


# 1. O SEU MODELO HTML DO CONTRATO
# Você pode estilizar com CSS básico aqui dentro
TEMPLATE_HTML_CONTRATO = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <style>
        @page {
            size: A4;
            margin: 2cm 2cm 2.5cm 2cm;
        }
        body { 
            font-family: Helvetica, Arial, sans-serif; 
            font-size: 10pt; 
            color: #000; 
            line-height: 1.4; 
        }
        .header { 
            text-align: center; 
            margin-bottom: 15px; 
        }
        .logo-text {
            font-size: 16pt;
            font-weight: bold;
            margin: 0;
            padding: 0;
        }
        .titulo { 
            text-align: center; 
            font-size: 12pt; 
            font-weight: bold; 
            text-decoration: underline;
            margin-bottom: 20px; 
        }
        .texto-justificado { 
            text-align: justify; 
            margin-bottom: 10px; 
        }
        /* Tabela de dados do aluno parecida com o original */
        .tabela-dados { 
            width: 100%; 
            border-collapse: collapse; 
            margin-bottom: 15px; 
        }
        .tabela-dados td { 
            padding: 3px 0; 
            vertical-align: bottom;
        }
        .linha-dado {
            border-bottom: 1px solid #000;
            display: inline-block;
            width: 100%;
        }
        .bold { 
            font-weight: bold; 
        }
        .clausula-titulo { 
            font-weight: bold; 
            margin-top: 15px; 
            margin-bottom: 5px; 
            text-decoration: underline;
        }
        .item-lista {
            margin-left: 20px;
            text-align: justify;
            margin-bottom: 5px;
        }
        /* Configuração das assinaturas para não quebrarem de página */
        .container-assinaturas { 
            width: 100%; 
            margin-top: 40px; 
            page-break-inside: avoid; 
        }
        .tabela-assinaturas {
            width: 100%;
            text-align: center;
            margin-top: 20px;
            border-collapse: collapse;
        }
        .tabela-assinaturas td {
            width: 50%;
            padding-top: 40px;
            padding-bottom: 10px;
        }
        .linha-assinatura { 
            border-top: 1px solid #000; 
            width: 80%; 
            margin: 0 auto; 
            padding-top: 5px; 
        }
        .data-local {
            text-align: center;
            margin-top: 30px;
            margin-bottom: 20px;
        }
    </style>
</head>
<body>

    <div class="header">
        <div class="logo-text">JAVIS® GAME ACADEMY</div>
    </div>

    <div class="titulo">
        Termo de Compromisso do Aluno
    </div>

    <div class="texto-justificado">
        Pelo presente instrumento particular, as partes a seguir qualificadas:<br>
        Por meios do <strong>INSTITUTO DO DESENVOLVIMENTO ECONÔMICO, TECNOLÓGICO E CULTURA - IDEC</strong>, sob CNPJ 19.136.591/0001-57, contratando a empresa abaixo para execução do projeto.<br>
        De um lado, PROJETO DE {{ curso }} pessoa jurídica de direito privado, inscrita no CNPJ sob o nº 46.422.995/0001-80 com sede em Av. Historiador Rubens de Mendonça, 1593, Bosque da Saúde - CEP: 78050-000 Cuiabá/MT, doravante denominada <strong>JAVIS GAME ACADEMY</strong>.
    </div>

    <table class="tabela-dados">
        <tr>
            <td colspan="2"><span class="bold">Aluno(a):</span> {{ aluno_nome }}</td>
            <td><span class="bold">Nasc.:</span> {{ aluno_nascimento }}</td>
        </tr>
        <tr>
            <td colspan="2"><span class="bold">CPF Aluno:</span> {{ aluno_cpf }}</td>
            <td><span class="bold">WhatsApp:</span> {{ whatsapp }}</td>
        </tr>
        <tr>
            <td colspan="3"><span class="bold">Endereço:</span> {{ endereco }} - <span class="bold">Bairro:</span> {{ bairro }} - <span class="bold">CEP:</span> {{ cep }}</td>
        </tr>
        <tr>
            <td style="width: 50%;"><span class="bold">Escola:</span> {{ escola_nome }}</td>
            <td style="width: 25%;"><span class="bold">Turno:</span> {{ escola_turno }}</td>
            <td style="width: 25%;"><span class="bold">Série:</span> {{ escola_serie }}</td>
        </tr>
        {% if responsavel_nome %}
        <tr>
            <td colspan="3" style="padding-top: 8px;"><span class="bold">Responsável Legal:</span> {{ responsavel_nome }}</td>
        </tr>
        <tr>
            <td colspan="2"><span class="bold">CPF Responsável:</span> {{ responsavel_cpf }}</td>
            <td><span class="bold">Grau Parentesco:</span> {{ responsavel_parentesco }}</td>
        </tr>
        {% endif %}
    </table>

    <div class="texto-justificado" style="margin-bottom: 15px;">
        Resolvem, de comum acordo, celebrar o presente Termo de Compromisso, mediante as cláusulas e condições seguintes:
    </div>

    <div class="clausula-titulo">Cláusula Primeira - Do Objeto</div>
    <div class="texto-justificado">
        O presente Termo tem como objeto a concessão de uma bolsa de estudo integral e gratuita para o(a) ALUNO(A) no curso de <strong>{{ curso }}</strong> 
        {% if curso == 'GAME DEV' %}
            com 60h de duração e 6 (seis) meses, 
        {% else %}
            com duração de 3 (três) meses, 
        {% endif %}
        promovido pelo PROJETO SOCIAL.
    </div>

    <div class="clausula-titulo">Cláusula Segunda - Das Condições do Curso</div>
    <div class="texto-justificado">
        1. O curso será ministrado com carga horária correspondente ao projeto escolhido.<br>
        2. As aulas ocorrerão na Av. Historiador Rubens de Mendonça, 1593, Bosque da Saúde - CEP 78050-000 - Cuiabá/MT - Presencial nos dias e horários estipulados pela coordenação.<br>
        3. O PROJETO DE CURSO DE {{ curso }} se compromete a oferecer a infraestrutura necessária para a realização do curso, incluindo material didático e acesso à plataforma, caso seja necessário.
    </div>

    <div class="clausula-titulo">Cláusula Terceira - Das Obrigações do(a) Aluno(a)</div>
    <div class="texto-justificado">
        O(A) ALUNO(A) compromete-se a:<br>
        <div class="item-lista">1. Frequentar as aulas e atividades do curso com assiduidade e pontualidade, buscando atingir a frequência facial mínima de 75% exigida para a conclusão do curso.</div>
        <div class="item-lista">2. Notas e trabalhos para receber a certificação, é necessárias notas igual ou superior a 7,0.</div>
        <div class="item-lista">3. Participar ativamente das atividades propostas, dedicando-se ao aprendizado e à realização das tarefas e trabalhos solicitados.</div>
        <div class="item-lista">4. Respeitar as regras e normas de convivência estabelecidas pelo PROJETO SOCIAL, bem como as diretrizes dos professores e coordenadores do curso.</div>
        <div class="item-lista">5. Zelar pelo patrimônio do PROJETO SOCIAL, utilizando de forma adequada os materiais e equipamentos disponibilizados.</div>
        <div class="item-lista">6. Comunicar antecipadamente ao PROJETO SOCIAL, sempre que possível, qualquer impossibilidade de comparecimento às aulas ou atividades.</div>
        <div class="item-lista">7. Manter uma postura ética e respeitosa com os colegas, professores e demais membros da equipe do PROJETO SOCIAL.</div>
        <div class="item-lista">8. Concluir o curso no prazo estabelecido, cumprindo com todas as exigências acadêmicas.</div>
    </div>

    <div class="clausula-titulo">Cláusula Quarta - Das Obrigações do Projeto Social</div>
    <div class="texto-justificado">
        O PROJETO SOCIAL compromete-se a:<br>
        <div class="item-lista">1. Oferecer o curso de forma gratuita, sem a cobrança de mensalidades ou taxas de matrícula.</div>
        <div class="item-lista">2. Disponibilizar professores qualificados e material didático adequado ao conteúdo programático.</div>
        <div class="item-lista">3. Emitir certificado de conclusão ao(à) ALUNO(A) que cumprir com todas as exigências do curso, incluindo frequência e desempenho satisfatório.</div>
        <div class="item-lista">4. Garantir um ambiente de aprendizado seguro e propício ao desenvolvimento do(a) ALUNO(A).</div>
    </div>

    <div class="clausula-titulo">Cláusula Quinta - Da Rescisão</div>
    <div class="texto-justificado">
        O presente Termo poderá ser rescindido, a qualquer tempo, por qualquer das partes, mediante aviso prévio de 15 dias por escrito.<br>
        O PROJETO SOCIAL poderá rescindir o Termo de imediato, sem prévio aviso, em caso de descumprimento grave de qualquer das obrigações assumidas pelo (a) ALUNO(A) na Cláusula Terceira, como por exemplo, mas não se limitando a:<br>
        <div class="item-lista">- Falta de frequência injustificada e excessiva.</div>
        <div class="item-lista">- Conduta inadequada ou desrespeitosa.</div>
        <div class="item-lista">- Danos intencionais ao patrimônio do PROJETO SOCIAL.</div>
    </div>

    <div class="clausula-titulo">Cláusula Sexta - Das Disposições Gerais</div>
    <div class="texto-justificado">
        <div class="item-lista">1. O presente Termo não gera qualquer vínculo empregatício ou obrigação trabalhista entre o PROJETO SOCIAL e o(a) ALUNO(A).</div>
        <div class="item-lista">2. Quaisquer alterações ou aditivos a este Termo deverão ser feitos por escrito e assinados por ambas as partes.</div>
        <div class="item-lista">3. As partes elegem o foro da Comarca de Cuiabá/MT para dirimir quaisquer dúvidas ou litígios decorrentes do presente Termo, com renúncia expressa a qualquer outro, por mais privilegiado que seja.</div>
    </div>

    <div class="container-assinaturas">
        <div class="texto-justificado">
            E, por estarem assim justos e contratados, as partes assinam o presente Termo de Compromisso em 2 (duas) vias de igual teor e forma, na presença das 2 (duas) testemunhas abaixo, para que produza seus devidos efeitos legais.
        </div>

        <div class="data-local">
            Cuiabá - MT, ______ de __________________________ de 20____.
        </div>

        <table class="tabela-assinaturas">
            <tr>
                <td>
                    <div class="linha-assinatura"></div>
                    <strong>ALUNO(A)</strong><br>
                    {% if responsavel_nome %} / RESPONSÁVEL LEGAL {% endif %}
                </td>
                <td>
                    <div class="linha-assinatura"></div>
                    <strong>PROJETO CURSO DE<br>{{ curso }}</strong>
                </td>
            </tr>
            <tr>
                <td>
                    <div class="linha-assinatura"></div>
                    <strong>Testemunha 1</strong><br>
                    RG:<br>
                    CPF:
                </td>
                <td>
                    <div class="linha-assinatura"></div>
                    <strong>Testemunha 2</strong><br>
                    RG:<br>
                    CPF:
                </td>
            </tr>
        </table>
    </div>

</body>
</html>
"""

# Dentro de @router.post("/gerar-contrato-html")
html_renderizado = template.render(
    curso=dados.curso,
    aluno_nome=dados.aluno_nome,
    aluno_cpf=dados.aluno_cpf,
    aluno_nascimento=dados.aluno_nascimento,
    whatsapp=dados.whatsapp,
    endereco=dados.endereco,
    bairro=dados.bairro,
    cep=dados.cep,
    escola_nome=dados.escola_nome,
    escola_turno=dados.escola_turno,
    escola_serie=dados.escola_serie,
    responsavel_nome=dados.responsavel_nome,
    responsavel_cpf=dados.responsavel_cpf,
    responsavel_parentesco=dados.responsavel_parentesco,
    # ADICIONE ESTES:
    responsavel_rg=dados.responsavel_rg,
    responsavel_rg_orgao=dados.responsavel_rg_orgao
)
 PDF: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/gerar-contrato-matricula")
def salvar_dados_contrato(dados: ContratoData, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)

    try:
        # Salva o registro na tb_geracao_termos
        resp = supabase.table("tb_geracao_termos").insert({
            "nome_responsavel": dados.responsavel_nome,
            "cpf_responsavel": dados.responsavel_cpf,
            "rg_responsavel": dados.rg_responsavel,
            "rg_orgao_expeditor": dados.rg_orgao_expeditor,
            "parentesco": dados.parentesco,
            "email_responsavel": dados.email_responsavel,
            "cep_responsavel": dados.cep_responsavel,
            "logradouro_responsavel": dados.logradouro_responsavel,
            "numero_responsavel": dados.numero_responsavel,
            "nome_aluno": dados.aluno_nome,
            "nickname_aluno": dados.nickname_aluno,
            "valor_total_negociado": dados.valor_total_negociado,
            "qtd_parcelas": dados.qtd_parcelas,
            "dia_vencimento": dados.dia_vencimento,
            "vendedor_responsavel": dados.vendedor_responsavel,
            "id_unidade": ctx['id_unidade']
        }).execute()
        
        return {"message": "Contrato registrado com sucesso!", "id": resp.data[0]['id']}
    except Exception as e:
        logger.error(f"Erro ao salvar contrato: {e}")
        raise HTTPException(status_code=400, detail=str(e))

