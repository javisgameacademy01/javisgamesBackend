"""
Rotas administrativas do sistema
"""
import os
from fastapi import APIRouter, HTTPException, Header, UploadFile, File, Form, Query, BackgroundTasks, Request
from pydantic import BaseModel
from supabase import create_client, Client
from datetime import datetime, timedelta, date
import time
import json
import requests
import logging
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
    AulaExperimentalUpdate,
    SprintPedagogicaData
)
ASAAS_API_KEY = os.getenv("ASAAS_API_KEY")
ASAAS_URL = "https://api.asaas.com/v3"

headers_asaas = {
    "access_token": ASAAS_API_KEY,
    "Content-Type": "application/json"
}

class ContratoData(BaseModel):
    # Campos que o Site e o Painel enviam
    curso: str
    aluno_nome: str
    horario_aula: Optional[str] = "A definir" 
    turma_codigo: Optional[str] = None
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
    valor_entrada: Optional[float] = 0.0


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

LOGO_JAVIS_BASE64 = "data:image/png;base64,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"
IMAGEM_FUNDO_BASE64 = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAfQAAAH0CAIAAABEtEjdAAAACXBIWXMAAA7EAAAOxAGVKw4bAAAFS2lUWHRYTUw6Y29tLmFkb2JlLnhtcAAAAAAAPD94cGFja2V0IGJlZ2luPSfvu78nIGlkPSdXNU0wTXBDZWhpSHpyZVN6TlRjemtjOWQnPz4KPHg6eG1wbWV0YSB4bWxuczp4PSdhZG9iZTpuczptZXRhLyc+CjxyZGY6UkRGIHhtbG5zOnJkZj0naHR0cDovL3d3dy53My5vcmcvMTk5OS8wMi8yMi1yZGYtc3ludGF4LW5zIyc+CgogPHJkZjpEZXNjcmlwdGlvbiByZGY6YWJvdXQ9JycKICB4bWxuczpBdHRyaWI9J2h0dHA6Ly9ucy5hdHRyaWJ1dGlvbi5jb20vYWRzLzEuMC8nPgogIDxBdHRyaWI6QWRzPgogICA8cmRmOlNlcT4KICAgIDxyZGY6bGkgcmRmOnBhcnNlVHlwZT0nUmVzb3VyY2UnPgogICAgIDxBdHRyaWI6Q3JlYXRlZD4yMDI2LTAzLTE2PC9BdHRyaWI6Q3JlYXRlZD4KICAgICA8QXR0cmliOkRhdGE+eyZxdW90O2RvYyZxdW90OzomcXVvdDtEQUhFSnZYaGEyTSZxdW90OywmcXVvdDt1c2VyJnF1b3Q7OiZxdW90O1VBRUNGQVdJSUJjJnF1b3Q7LCZxdW90O2JyYW5kJnF1b3Q7OiZxdW90O0JBRUNGT0ZPcnVRJnF1b3Q7fTwvQXR0cmliOkRhdGE+CiAgICAgPEF0dHJpYjpFeHRJZD4yNTFjOTJlNy1mNGU0LTQ4YzctOTdhNy00MGMyNWRkOTczZTU8L0F0dHJpYjpFeHRJZD4KICAgICA8QXR0cmliOkZiSWQ+NTI1MjY1OTE0MTc5NTgwPC9BdHRyaWI6RmJJZD4KICAgICA8QXR0cmliOlRvdWNoVHlwZT4yPC9BdHRyaWI6VG91Y2hUeXBlPgogICAgPC9yZGY6bGk+CiAgIDwvcmRmOlNlcT4KICA8L0F0dHJpYjpBZHM+CiA8L3JkZjpEZXNjcmlwdGlvbj4KCiA8cmRmOkRlc2NyaXB0aW9uIHJkZjphYm91dD0nJwogIHhtbG5zOmRjPSdodHRwOi8vcHVybC5vcmcvZGMvZWxlbWVudHMvMS4x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"

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
            margin-bottom: 25px; 
        }
        .logo-text {
            font-size: 18pt;
            font-weight: bold;
            margin: 0;
            padding: 0;
        }
        .titulo { text-align: center; font-size: 12pt; font-weight: bold; text-decoration: underline; margin-bottom: 20px; }
        .texto-justificado { text-align: justify; margin-bottom: 10px; }
        .tabela-dados { width: 100%; border-collapse: collapse; margin-bottom: 15px; }
        .tabela-dados td { padding: 3px 0; vertical-align: bottom; }
        .bold { font-weight: bold; }
        .clausula-titulo { font-weight: bold; margin-top: 15px; margin-bottom: 5px; text-decoration: underline; }
        .item-lista { margin-left: 20px; text-align: justify; margin-bottom: 5px; }
        .container-assinaturas { width: 100%; margin-top: 40px; page-break-inside: avoid; }
        .tabela-assinaturas { width: 100%; text-align: center; margin-top: 20px; border-collapse: collapse; }
        .tabela-assinaturas td { width: 50%; padding-top: 40px; padding-bottom: 10px; }
        .linha-assinatura { border-top: 1px solid #000; width: 80%; margin: 0 auto; padding-top: 5px; }
        .data-local { text-align: center; margin-top: 30px; margin-bottom: 20px; }
    </style>
</head>
<body>
    <div class="header">
        <div class="logo-text">JAVIS® GAME ACADEMY</div>
    </div>

    <div class="titulo">Termo de Compromisso do Aluno</div>

    <div class="texto-justificado">
        Pelo presente instrumento particular, as partes a seguir qualificadas:<br>
        Por meios do <strong>INSTITUTO DO DESENVOLVIMENTO ECONÔMICO, TECNOLÓGICO E CULTURA - IDEC</strong>, sob CNPJ 19.136.591/0001-57, contratando a empresa abaixo para execução do projeto.<br>
        De um lado, <strong>PROJETO {{ curso_oficial }}</strong> pessoa jurídica de direito privado, inscrita no CNPJ sob o nº 46.422.995/0001-80 com sede em Av. Historiador Rubens de Mendonça, 1593, Bosque da Saúde - CEP 78050-000- Cuiabá/MT, doravante denominada <strong>JAVIS GAME ACADEMY</strong>.
    </div>

    <table class="tabela-dados">
        <tr><td colspan="2"><span class="bold">Aluno:</span> {{ aluno_nome }}</td><td><span class="bold">Nasc.:</span> {{ aluno_nascimento }}</td></tr>
        <tr><td colspan="2"><span class="bold">CPF Aluno:</span> {{ aluno_cpf }}</td><td><span class="bold">WhatsApp:</span> {{ whatsapp }}</td></tr>
        <tr><td colspan="3"><span class="bold">Endereço:</span> {{ endereco }} - <span class="bold">Bairro:</span> {{ bairro }} - <span class="bold">CEP:</span> {{ cep }}</td></tr>
        <tr><td style="width: 50%;"><span class="bold">Nome da Escola:</span> {{ escola_nome }}</td><td style="width: 25%;"><span class="bold">Turno:</span> {{ escola_turno }}</td><td style="width: 25%;"><span class="bold">Série:</span> {{ escola_serie }}</td></tr>
        {% if responsavel_nome %}
        <tr><td colspan="3" style="padding-top: 8px;"><span class="bold">Responsável Legal:</span> {{ responsavel_nome }}</td></tr>
        <tr><td colspan="2"><span class="bold">CPF:</span> {{ responsavel_cpf }} {% if responsavel_rg %} | <span class="bold">RG:</span> {{ responsavel_rg }} {{ responsavel_rg_orgao }} {% endif %}</td><td><span class="bold">Grau Parentesco:</span> {{ responsavel_parentesco }}</td></tr>
        {% endif %}
    </table>

    <div class="texto-justificado">Resolvem, de comum acordo, celebrar le presente Termo de Compromisso, mediante as cláusulas e condições seguintes:</div>

    <div class="clausula-titulo">Cláusula Primeira - Do Objeto</div>
    <div class="texto-justificado">
        O presente Termo tem como objeto a concessão de uma bolsa de estudo integral e gratuita para o(a) ALUNO(A) no curso de <strong>{{ curso_oficial }}</strong> 
        {% if curso == 'GAME DEV' %} com 60h de duração e 6 (seis) meses, {% else %} com duração de 3 (três) meses, {% endif %} promovido pelo PROJETO SOCIAL.
    </div>

    <div class="clausula-titulo">Clausula Segunda – Das Condições do Curso</div>
    <div class="texto-justificado">
        1. O curso será ministrado de _____/____/_______ a _____/____/_______, 
        {% if curso == 'GAME DEV' %} com carga horária de 60 horas, distribuídas em 24 aulas.<br> {% else %} com carga horária de 30 horas, distribuídas em 12 aulas.<br> {% endif %}
        2. As aulas ocorrerão na Av. Historiador Rubens de Mendonça, 1593, Bosque da Saúde CEP 78050-000- Cuiabá/MT - Presencial nos dias e horários abaixo;<br>
        <strong>Horário das Aulas: 
            {% if horario_aula == 'A combinar' or horario_aula == 'A combinar com a coordenação' or not horario_aula %}
                ________________________________________
            {% else %}
                {{ horario_aula }}
            {% endif %}
        </strong><br>
        3. O PROJETO DE CURSO DE <strong>{{ curso_oficial }}</strong> se compromete a oferecer a infraestrutura necessária para a realização do curso, incluindo material didático e acesso à plataforma, caso seja necessário.
    </div>

    <div class="clausula-titulo">Cláusula Terceira - Das Obrigações do(a) Aluno(a)</div>
    <div class="texto-justificado">
        O(A) ALUNO(A) compromete-se a:
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
        O PROJETO SOCIAL compromete-se a:
        <div class="item-lista">1. Oferecer o curso de forma gratuita, sem a cobrança de mensalidades ou taxas de matrícula.</div>
        <div class="item-lista">2. Disponibilizar professores qualificados e material didático adequado ao conteúdo programático.</div>
        <div class="item-lista">3. Emitir certificado de conclusão ao(à) ALUNO(A) que cumprir com todas as exigências do curso, incluindo frequência e desempenho satisfatório.</div>
        <div class="item-lista">4. Garantir um ambiente de aprendizado seguro e propício ao desenvolvimento do(a) ALUNO(A).</div>
    </div>

    <div class="clausula-titulo">Cláusula Quinta - Da Rescisão</div>
    <div class="texto-justificado">
        O presente Termo poderá ser rescindido, a qualquer tempo, por qualquer das partes, mediante aviso prévio de 15 dias por escrito.<br>
        O PROJETO SOCIAL poderá rescindir o Termo de imediato, sem prévio aviso, em caso de descumprimento grave de qualquer das obrigações assumidas pelo (a) ALUNO(A) na Cláusula Terceira, como por exemplo, mas não se limitando a:<br>
        <div class="item-lista">• Falta de frequência injustificada e excessiva.</div>
        <div class="item-lista">• Conduta inadequada ou desrespeitosa.</div>
        <div class="item-lista">• Danos intencionais ao patrimônio do PROJETO SOCIAL.</div>
    </div>

    <div class="clausula-titulo">Cláusula Sexta - Das Disposições Gerais</div>
    <div class="texto-justificado">
        <div class="item-lista">1. O presente Termo não gera qualquer vínculo empregatício ou obrigação trabalhista entre o PROJETO SOCIAL e o(a) ALUNO(A).</div>
        <div class="item-lista">2. Quaisquer alterações ou aditivos a este Termo deverão ser feitos por escrito e assinados por ambas as partes.</div>
        <div class="item-lista">3. As partes elegem o foro da Comarca de Cuiabá/MT para dirimir quaisquer dúvidas ou litígios decorrentes do presente Termo, com renúncia expressa a qualquer outro, por mais privilegiado que seja.</div>
    </div>

    <div class="container-assinaturas">
        <div class="texto-justificado">E, por estarem assim justos e contratados, as partes assinam le presente Termo de Compromisso em 2 (duas) vias de igual teor e forma, na presença das 2 (duas) testemunhas abaixo, para que produza seus devidos efeitos legais.</div>
        <div class="data-local">Cuiabá - MT, ______ de __________________________ de 2026.</div>
        <table class="tabela-assinaturas">
            <tr>
                <td><div class="linha-assinatura"></div><strong>ALUNO(A) / RESPONSÁVEL LEGAL</strong></td>
                <td><div class="linha-assinatura"></div><strong>PROJETO CURSO DE<br>{{ curso_oficial }}</strong></td>
            </tr>
            <tr>
                <td><div class="linha-assinatura"></div><strong>Testemunhas 1</strong><br>RG: / CPF:</td>
                <td><div class="linha-assinatura"></div><strong>Testemunhas 2</strong><br>RG: / CPF:</td>
            </tr>
        </table>
    </div>
</body>
</html>
"""

@router.post("/gerar-contrato-html")
async def gerar_contrato_endpoint(dados: ContratoData, authorization: str = Header(None)):
    try:
        # Pega exatamente o nome que o vendedor escolheu (ex: PERFORMANCE GAME ou GAME DEV)
        nome_oficial_curso = dados.curso.upper()

        horario_limpo = dados.horario_aula
        if not horario_limpo or horario_limpo in ["A definir", "A combinar", "A combinar com a coordenação"]:
            horario_limpo = "A combinar"

        template = Template(TEMPLATE_HTML_CONTRATO)
        html_renderizado = template.render(
            curso=dados.curso,
            curso_oficial=nome_oficial_curso,
            horario_aula=horario_limpo, 
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
            responsavel_rg=dados.responsavel_rg,
            responsavel_rg_orgao=dados.responsavel_rg_orgao
        )

        pdf_file = io.BytesIO()
        pisa.CreatePDF(io.StringIO(html_renderizado), dest=pdf_file)
        pdf_bytes = pdf_file.getvalue()

        nome_arquivo = f"Contrato_{dados.aluno_nome.replace(' ', '_')}_{int(time.time())}.pdf"
        supabase.storage.from_("termos").upload(nome_arquivo, pdf_bytes, file_options={"content-type": "application/pdf", "upsert": "true"})
        url_pdf = supabase.storage.from_("termos").get_public_url(nome_arquivo)

        dados_db = dados.model_dump()
        dados_db["url_pdf"] = url_pdf
        dados_db["visualizado"] = False 
        supabase.table("tb_geracao_termos").insert(dados_db).execute()

        return {"status": "success", "url_pdf": url_pdf}

    except Exception as e:
        logger.error(f"Erro ao gerar contrato PDF: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/atualizar-pdfs-antigos")
async def regenerar_todos_os_contratos():
    try:
        contratos = supabase.table("tb_geracao_termos").select("*").execute().data
        if not contratos: return {"message": "Nenhum registo encontrado."}

        atualizados = 0
        template = Template(TEMPLATE_HTML_CONTRATO)

        for d in contratos:
            c_nome = d.get("curso", "").upper()
            oficial = c_nome
            
            horario_aluno = d.get("horario_aula")
            if not horario_aluno or horario_aluno in ["A definir", "A combinar", "A combinar com a coordenação"]:
                horario_aluno = "A combinar"

            html = template.render(
                curso=c_nome,
                curso_oficial=oficial,
                horario_aula=horario_aluno,
                aluno_nome=d.get("aluno_nome"),
                aluno_cpf=d.get("aluno_cpf"),
                aluno_nascimento=d.get("aluno_nascimento"),
                whatsapp=d.get("whatsapp"),
                endereco=d.get("endereco"),
                bairro=d.get("bairro"),
                cep=d.get("cep"),
                escola_nome=d.get("escola_nome"),
                escola_turno=d.get("escola_turno"),
                escola_serie=d.get("escola_serie"),
                responsavel_nome=d.get("responsavel_nome"),
                responsavel_cpf=d.get("responsavel_cpf"),
                responsavel_parentesco=d.get("responsavel_parentesco"),
                responsavel_rg=d.get("responsavel_rg"),
                responsavel_rg_orgao=d.get("responsavel_rg_orgao")
            )

            pdf_io = io.BytesIO()
            pisa.CreatePDF(io.StringIO(html), dest=pdf_io)
            
            f_nome = f"Refeito_{d['id']}_{int(time.time())}.pdf"
            supabase.storage.from_("termos").upload(f_nome, pdf_io.getvalue(), file_options={"content-type": "application/pdf"})
            nova_url = supabase.storage.from_("termos").get_public_url(f_nome)
            
            supabase.table("tb_geracao_termos").update({"url_pdf": nova_url}).eq("id", d["id"]).execute()
            atualizados += 1
            time.sleep(0.3)

        return {"status": "success", "message": f"{atualizados} contratos limpos e padronizados!"}
    except Exception as e:
        logger.error(f"Erro na regeneração em massa: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# =========================================
# GESTÃO DE TERMOS E CONTRATOS (PAINEL)
# =========================================

@router.get("/listar-termos")
def listar_todos_os_termos(authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)
    
    # Restrição: Apenas Comercial (3) e Gerência (8+) podem ver os contratos
    if ctx["nivel"] != 3 and ctx["nivel"] < 8: 
        raise HTTPException(status_code=403, detail="Acesso restrito.")

    try:
        # Busca todos os contratos gerados, do mais recente para o mais antigo
        return supabase.table("tb_geracao_termos").select("*").order("created_at", desc=True).execute().data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/termo/{id_termo}/visto")
def marcar_termo_como_visto(id_termo: int, dados: dict, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)
    
    if ctx["nivel"] != 3 and ctx["nivel"] < 8: raise HTTPException(status_code=403)

    try:
        visualizado = dados.get("visualizado", True)
        supabase.table("tb_geracao_termos").update({"visualizado": visualizado}).eq("id", id_termo).execute()
        return {"message": "Contrato marcado como visualizado."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/termo/{id_termo}/matricula")
def alternar_status_matricula_termo(id_termo: int, dados: dict, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)
    
    if ctx["nivel"] != 3 and ctx["nivel"] < 8: raise HTTPException(status_code=403)

    try:
        matriculado = dados.get("matriculado", False)
        supabase.table("tb_geracao_termos").update({"matriculado": matriculado}).eq("id", id_termo).execute()
        return {"message": "Status de matrícula atualizado com sucesso."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ========================================================
# CONTRATO PRIVADO (COMERCIAL / PAGANTES)
# ========================================================

TEMPLATE_HTML_CONTRATO_PRIVADO = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <style>
        @page { size: A4; margin: 2cm 2cm 2.5cm 2cm; }
        body { font-family: Helvetica, Arial, sans-serif; font-size: 10pt; color: #000; line-height: 1.4; }
        
        .header { text-align: center; margin-bottom: 20px; border-bottom: 2px solid #000; padding-bottom: 10px; }
        .logo-text { font-size: 16pt; font-weight: bold; margin: 0; padding: 0; }
        .sub-header { font-size: 11pt; font-weight: bold; margin-top: 5px; }
        
        .texto-justificado { text-align: justify; margin-bottom: 10px; }
        .bold { font-weight: bold; }
        
        .tabela-dados { width: 100%; border-collapse: collapse; margin-bottom: 15px; font-size: 9pt; }
        .tabela-dados td { border: 1px solid #ccc; padding: 5px; vertical-align: middle; }
        .tabela-dados th { background-color: #f3f4f6; text-align: left; padding: 5px; border: 1px solid #ccc; }
        
        .clausula-titulo { font-weight: bold; margin-top: 15px; margin-bottom: 5px; text-decoration: underline; font-size: 10pt; }
        
        /* ESTILOS DO CARNÊ (IMPRESSÃO) */
        .quebra-pagina { page-break-before: always; }
        .titulo-carne { text-align: center; font-size: 16pt; font-weight: bold; border-bottom: 2px dashed #000; margin-bottom: 20px; padding-bottom: 10px; }
        .carne-card { width: 100%; border: 1px solid #000; margin-bottom: 15px; border-radius: 5px; page-break-inside: avoid; }
        .carne-header { background-color: #000; color: #fff; font-weight: bold; padding: 5px 10px; font-size: 11pt; }
        .carne-body { padding: 10px; display: table; width: 100%; }
        .carne-linha { margin-bottom: 8px; font-size: 10pt; border-bottom: 1px dotted #ccc; padding-bottom: 2px; }
        .assinatura-carne { text-align: right; margin-top: 20px; border-top: 1px solid #000; display: inline-block; width: 250px; padding-top: 5px; font-size: 8pt; float: right; }
    </style>
</head>
<body>
    <div class="header">
        <div class="logo-text">JAVIS GAME ACADEMY - UNIDADE CUIABÁ</div>
        <div class="sub-header">CONTRATO DE PRESTAÇÃO DE SERVIÇOS DE TREINAMENTO PROFISSIONAL</div>
    </div>

    <div class="texto-justificado">
        Pelo presente instrumento particular de contrato de prestação de serviços de treinamento profissional em computação a, <strong>AJA EDUCAÇÃO E ENTRETENIMENTO LTDA</strong>, doravante denominada <strong>ESCOLA</strong>, pessoa jurídica de direito privado, inscrita no CNPJ sob nº 46.422.995/0001-80 com sede na Avenida Historiador Rubens de Mendonça 1593, Bairro Bosque da Saúde, Cuiabá - MT.
    </div>

    <table class="tabela-dados">
        <tr><th colspan="4">DADOS DO CONTRATANTE (RESPONSÁVEL FINANCEIRO)</th></tr>
        <tr>
            <td colspan="2"><span class="bold">Nome:</span> {{ responsavel_nome if responsavel_nome else aluno_nome }}</td>
            <td><span class="bold">CPF:</span> {{ responsavel_cpf if responsavel_cpf else aluno_cpf }}</td>
            <td><span class="bold">RG:</span> {{ responsavel_rg }}</td>
        </tr>
        <tr>
            <td colspan="2"><span class="bold">Endereço:</span> {{ endereco }}</td>
            <td><span class="bold">Bairro:</span> {{ bairro }}</td>
            <td><span class="bold">CEP:</span> {{ cep }}</td>
        </tr>
        <tr>
            <td colspan="4"><span class="bold">Telefone/WhatsApp:</span> {{ whatsapp }}</td>
        </tr>
    </table>

    <table class="tabela-dados">
        <tr><th colspan="3">DADOS DO BENEFICIÁRIO (ALUNO)</th></tr>
        <tr>
            <td colspan="2"><span class="bold">Nome:</span> {{ aluno_nome }}</td>
            <td><span class="bold">Data de Nasc.:</span> {{ aluno_nascimento }}</td>
        </tr>
        <tr>
            <td colspan="3"><span class="bold">Curso Contratado:</span> {{ curso_oficial }} | <span class="bold">Horário:</span> {{ horario_aula }}</td>
        </tr>
    </table>

    <div class="texto-justificado">Resolvem contratar sob as seguintes cláusulas:</div>

    <div class="clausula-titulo">CLÁUSULA PRIMEIRA:</div>
    <div class="texto-justificado">O BENEFICIÁRIO ingressará no programa de capacitação profissional em computação que será ministrado nas dependências da ESCOLA.</div>

    <div class="clausula-titulo">CLÁUSULA SEGUNDA:</div>
    <div class="texto-justificado">
        {% if curso_oficial == 'GAME DEV' %}
            A duração deste programa de capacitação é de 12 (Doze) meses, dividido em 7 (sete) módulos, com total de 120 (Cento e vinte) horas aulas.
        {% elif curso_oficial == 'DESIGN START' or curso_oficial == 'DESIGNER START' %}
            A duração deste programa de capacitação é de 12 (Doze) meses, dividido em 9 (nove) módulos, com total de 120 (Cento e vinte) horas aulas.
        {% elif curso_oficial == 'GAME PRO' %}
            A duração deste programa de capacitação é de 12 (Doze) meses, dividido em 5 (cinco) módulos, com total de 120 (Cento e vinte) horas aulas.
        {% else %}
            A duração deste programa de capacitação é de 12 (Doze) meses.
        {% endif %}
    </div>

    <div class="clausula-titulo">CLÁUSULA TERCEIRA:</div>
    <div class="texto-justificado">
        As unidades e suas disciplinas estão dispostas das seguintes maneiras:<br><br>
        
        {% if curso_oficial == 'GAME DEV' %}
            <span class="bold">MÓDULO 1</span> — SCRATCH + PORTUGOL<br>
            <span class="bold">MÓDULO 2</span> — PYTHON GAME (Pygame)<br>
            <span class="bold">MÓDULO 3</span> — NO-CODE PRO (GDevelop)<br>
            <span class="bold">MÓDULO 4</span> — INDIE POWER (Godot)<br>
            <span class="bold">MÓDULO 5</span> — CLÁSSICO 2D (GameMaker)<br>
            <span class="bold">MÓDULO 6</span> — PADRÃO DE INDÚSTRIA (Unity)<br>
            <span class="bold">MÓDULO 7</span> — ALTO DESEMPENHO (Unreal Engine)
            
        {% elif curso_oficial == 'DESIGN START' or curso_oficial == 'DESIGNER START' %}
            <span class="bold">1️⃣ Módulo</span> — Introdução<br>
            <span class="bold">2️⃣ Módulo</span> — Photoshop<br>
            <span class="bold">3️⃣ Módulo</span> — Illustrator<br>
            <span class="bold">4️⃣ Módulo</span> — InDesign<br>
            <span class="bold">5️⃣ Módulo</span> — Premiere Pro<br>
            <span class="bold">6️⃣ Módulo</span> — After Effects<br>
            <span class="bold">7️⃣ Módulo</span> — Animate<br>
            <span class="bold">8️⃣ Módulo</span> — Blender<br>
            <span class="bold">9️⃣ Módulo</span> — Cinema 4D
            
        {% elif curso_oficial == 'GAME PRO' %}
            <span class="bold">MÓDULO 1</span> – FUNDAMENTOS DO PRO-PLAYER<br>
            <span class="bold">MÓDULO 2</span> – PRO PLAYER 1<br>
            <span class="bold">MÓDULO 3</span> – E-SPORTS E DESIGNER GAMER<br>
            <span class="bold">MÓDULO 4</span> – PRO PLAYER 2 E STREAMER<br>
            <span class="bold">MÓDULO 5</span> – PRO PLAYER AVANÇADO E INGLÊS BÁSICO
            
        {% else %}
            <span class="bold">MÓDULO ÚNICO</span> - {{ curso_oficial }}
        {% endif %}
    </div>
    <div class="clausula-titulo">CLÁUSULA QUARTA:</div>
    <div class="texto-justificado">O BENEFICIÁRIO começará o seu treinamento OBRIGATORIAMENTE PELA PRIMEIRA UNIDADE.</div>
    <div class="paragrafo">Parágrafo único: Após o término de uma unidade, o aluno (Beneficiário) estará AUTOMATICAMENTE inscrito na unidade seguinte, até que se completem todos os módulos.</div>

    <div class="clausula-titulo">CLÁUSULA QUINTA:</div>
    <div class="texto-justificado">O material didático referente a UNIDADE será disponibilizado através da plataforma on-line da ESCOLA, sendo disponibilizado no ato da matrícula o login e senha para acesso ao portal, tendo a ESCOLA até o início das aulas para liberar o acesso do BENEFICIÁRIO ao portal do aluno.</div>

    <div class="clausula-titulo">CLÁUSULA SEXTA:</div>
    <div class="texto-justificado">A data de início do treinamento, bem como disciplinas, dias e horários das novas turmas, terão início com prazo de até 90 dias após assinatura do contrato e serão informadas ao contratante e/ou beneficiário através de circulares, avisos em sala, contato via whatsApp, rede sociais e afixos em quadro de avisos e telefonemas, mensagens de aplicativos, ficando reservado à escola dispor de horários, turmas e disciplinas em conformidade com o que lhe for mais apropriado.</div>

    <div class="clausula-titulo">CLÁUSULA SÉTIMA:</div>
    <div class="texto-justificado">As aulas se darão em uma sala com microcomputadores, tantos quantos forem necessários para que não ultrapasse a razão de 01(um) aluno para cada computador.</div>

    <div class="clausula-titulo">CLÁUSULA OITAVA:</div>
    <div class="texto-justificado">A Escola promoverá avaliações para verificar o aproveitamento dos alunos, onde somente farão jus ao certificado, o aluno que possuir nota maior ou igual a 7,0 (sete) e frequência maior que 75% (Setenta e cinco por cento) da carga horária do curso.</div>
    <div class="paragrafo">Parágrafo primeiro: Apenas caberá ao aluno, recurso disponibilizado pela direção acadêmica da Escola contratada. Até no máximo 90 (noventa) dias da conclusão do curso a escola fará a entrega do certificado ao aluno, devendo o mesmo estar com todos os pagamentos quitados.</div>

    <div class="clausula-titulo">CLÁUSULA NONA:</div>
    <div class="texto-justificado">A FALTA DE FREQÜÊNCIA do aluno ao curso contratado, NÃO O EXIME DO PAGAMENTO DAS PARCELAS, tendo em vista a disponibilidade do serviço colocado ao contratante, salvo os casos de desistências formalmente requeridos através de protocolo.</div>

    <div class="clausula-titulo">CLÁUSULA DÉCIMA:</div>
    <div class="texto-justificado">Caso o BENEFICIÁRIO falte às aulas e justifique suas faltas através de documentação competente, a ESCOLA providenciará dentro do possível, horários para reposição das mesmas com pagamento de forma antecipada no valor R$100,00 por aula.</div>

    <div class="clausula-titulo">CLÁUSULA DÉCIMA PRIMEIRA:</div>
    <div class="texto-justificado">Como contraprestação pelos serviços contratados, o contratante pagará mediante a assinatura do contrato o valor da Matricula de R$ 700 (setecentos reais), Material Plataforma R$2.500,00 (Dois mil e quinhentos reais), práticas pedagógicas R$1.000,00 (mil reais) e 12 (doze) parcelas iguais e consecutivas de R$790,90 (Setecentos e noventa e nove reais).<br>A PRIMEIRA PARCELA SERÁ PAGA ANTECIPADAMENTE COMO RESERVA E SINAL DE NEGÓCIO NÃO SENDO DEVOLVIDA SOB HIPÓTESE ALGUMA.</div>

    <div class="clausula-titulo">CLÁUSULA DÉCIMA SEGUNDA:</div>
    <div class="texto-justificado">Através do PROGRAMA de INCENTIVO INTERNO este CONTRATANTE está sendo beneficiado pelo SISTEMA DE BOLSA PARCIAL e, portanto, terá desconto lhe proporcionando o valor de matrícula de R$300,00 e de 12x de R$385,00 no boleto.</div>
    <div class="paragrafo">Parágrafo único: pagando até o dia 8 de cada mês, o aluno terá um bônus de pontualidade de R$60,00.</div>

    <div class="clausula-titulo">CLÁUSULA DÉCIMA TERCEIRA:</div>
    <div class="texto-justificado">Os valores acima poderão sofrer reajustes caso ocorra mudanças na realidade econômica do país, como inflação ou outra forma de desvalorização monetária, sendo nestes casos aplicados os índices de reajustes mensais ou em outra periodicidade que mantenha o equilíbrio econômico do contrato.</div>

    <div class="clausula-titulo">CLÁUSULA DÉCIMA QUARTA:</div>
    <div class="texto-justificado">O contratante poderá suspender por até 60 (sessenta) dias a prestação de serviço mediante requerimento de trancamento da matricula do beneficiário na secretaria da escola e pagamento da taxa administrativa no valor de R$100,00 mais uma parcela sem bônus, sendo observadas as formalidades contidas nos parágrafos que seguem:</div>
    <div class="paragrafo">Parágrafo primeiro: O requerimento do trancamento só será deferido caso o contratante esteja com o mês corrente devidamente pago, mesmo que ainda não tenha chegado a data do vencimento.</div>
    <div class="paragrafo">Parágrafo segundo: O trancamento apenas poderá ser realizado, ao fim de alguma das disciplinas.</div>
    <div class="paragrafo">Parágrafo terceiro: Durante o período do trancamento, caso o aluno tenha financiado o curso por meio de alguma financeira o pagamento das parcelas por parte do contratante NÃO FICARÁ SUSPENSO, salvo sob autorização por parte da escola.</div>

    <div class="clausula-titulo">CLÁUSULA DÉCIMA QUINTA:</div>
    <div class="texto-justificado">As TRANSFERÊNCIAS, DESISTÊNCIAS E RESCISÃO CONTRATUAL, somente poderão ser solicitadas pelos contratantes que estiverem com o pagamento das parcelas em dia e através de comunicação por escrito para a secretaria da contratada com antecedência mínima de 15 (quinze) dias.</div>
    <div class="paragrafo">Parágrafo primeiro: Na transferência de benefícios, será cobrada uma taxa administrativa no valor de R$150,00 (cento e cinquenta reais).</div>
    <div class="paragrafo">Parágrafo segundo: No caso de desistência ou rescisão contratual, o contratante deverá pagar: I – Está adimplente com qualquer vencimento com a escola ou financeiras respeitar o prazo de 15 (quinze) dias previsto no caput da presente cláusula; II – multa de 20% (vinte por cento) sobre o período restante que não será cursado, como cláusula compensatória prevista no artigo 408 e seguintes do Código Civil. Para cálculo da presente multa, NÃO serão consideradas as parcelas com o abatimento de que trata a cláusula décima segunda deste contrato, tendo a ESCOLA o prazo de até 90 (noventa) dias para proceder o estorno de eventual crédito ao beneficiário. Para cálculo da multa contratual, não será feita em cima de valores de ofertas, bolsas ou descontos promocionais e sim, em valores tabelados do curso do ano de exercício da clausula décima primeira; III – É facultado ao Beneficiário compensar eventual crédito decorrente da rescisão do contrato nas dependências da ESCOLA com outros serviços como GAMER PARTY (festas de aniversários), PASSAPORTES, SÓCIO JOGADOR E OUTROS.</div>

    <div class="clausula-titulo">CLÁUSULA DÉCIMA SEXTA:</div>
    <div class="texto-justificado">Não fica caracterizado como rescisão do contrato ou suspensão da prestação de serviço, o abandono das aulas ou a falta de ingresso em uma turma após o término de uma disciplina por meio do aluno.</div>

    <div class="clausula-titulo">CLÁUSULA DÉCIMA SÉTIMA:</div>
    <div class="texto-justificado">Em caso de troca de turma por iniciativa do beneficiário, será paga uma taxa no valor de R$ 35,00 (Trinta e Cinco reais).</div>

    <div class="clausula-titulo">CLÁUSULA DÉCIMA OITAVA:</div>
    <div class="texto-justificado">Caso haja redução da turma em 50% (cinquenta por cento) por desistência ou outro motivo qualquer, haverá remanejamento da mesma para outros dias e horários, não sendo este motivo para rescisão contratual por culpa do contratado.</div>

    <div class="clausula-titulo">CLÁUSULA DÉCIMA NONA:</div>
    <div class="texto-justificado">O CONTRATANTE se obriga a comunicar à CONTRATADA a eventual mudança de endereço, através de requerimento protocolizado no Atendimento ao Cliente da unidade, bem como atualizar seus dados cadastrais sempre que houver alguma alteração.</div>
    <div class="paragrafo">Parágrafo único: A falta de comunicação de que trata este artigo sujeitará o CONTRATANTE a arcar com todos os prejuízos que essa omissão acarretar.</div>

    <div class="clausula-titulo">CLÁUSULA VIGÉSIMA:</div>
    <div class="texto-justificado">A CONTRATADA não se responsabilizará perante o CONTRATANTE por qualquer perda, danos, extravio ou furto de objetos e/ou veículos em suas dependências.</div>

    <div class="clausula-titulo">CLÁUSULA VIGÉSIMA PRIMEIRA:</div>
    <div class="texto-justificado">Não será permitido acompanhante de qualquer idade em sala de aula, salvo as pessoas com deficiência (s), mediante comprovação e autorização antecipada da gerência da unidade educativa.</div>

    <div class="clausula-titulo">CLÁUSULA VIGÉSIMA SEGUNDA:</div>
    <div class="texto-justificado">Não é permitida a entrada e a permanência de pessoas portando qualquer tipo de armas, bebidas alcoólicas e substâncias proibidas por lei nas dependências da CONTRATADA.</div>
    <div class="paragrafo">Parágrafo primeiro: (Obrigações do aluno) o aluno beneficiário deste contrato deverá observar os princípios, comportamento e conduta ética, moral, disciplinar e de respeito às normas de boa convivência coletiva e a qualquer integrante da comunidade escolar, necessários e compatíveis ao desenvolvimento da educação e ensino, sob pena de expulsão do estabelecimento de ensino, fazendo assim o termino de contrato isentando a contratada de qualquer valor de taxas ou multas. A instituição possui exposto na entrada dos laboratórios o manual de regras das dependências da unidade JAVIS GAME ACADEMY para apreciação de qualquer aluno ou responsável. É de responsabilidade do ALUNO CONTRATANTE, zelar pelos computadores e tabletes disponibilizados para execução de sua aula. Em caso de perda, quebra ou extravio será de responsabilidade do CONTRATANTE em uso arcar com os prejuízos causados na escola.</div>

    <div class="clausula-titulo">CLÁUSULA VIGÉSIMA TERCEIRA:</div>
    <div class="texto-justificado">O atraso no pagamento das mensalidades importará em multa de 2% (dois por cento) sobre a parcela devida, juros de mora de 0,033% (zero virgula zero trinta e três por cento) pro rata dia e correção monetária, podendo o contratante inscrever o nome do devedor no serviço de Proteção ao Crédito (SCPC) e SERASA.</div>

    <div class="clausula-titulo">CLÁUSULA VIGÉSIMA QUARTA:</div>
    <div class="texto-justificado">Havendo inadimplemento no pagamento das parcelas, eventualmente, tendo a CONTRATADA se utilizada de serviços advocatícios para a cobrança de valores em aberto, o CONTRATANTE pagará honorários advocatícios extrajudiciais em percentual não superior a 10% (dez por cento) do valor total do débito, nos termos do at. 4º, 6º - III, 54, parágrafo 4º da lei 8.078/90 (CDC) e art. 22 lei 8906/94, podendo a CONTRATADA, socorrer-se de todos os meios legais necessários à satisfação de seus direitos.</div>

    <div class="clausula-titulo">CLÁUSULA VIGÉSIMA QUINTA:</div>
    <div class="texto-justificado">O contratante afirma, neste ato, que LEU E ENTENDEU CLARAMENTE o presente contrato e todas suas cláusulas, concordando e aceitando todos os seus termos.</div>

    <div style="margin-top: 50px; text-align: center; page-break-inside: avoid;">
        <p>Cuiabá - MT, ______ de __________________________ de 2026.</p>
        <br><br><br>
        <table style="width: 100%; text-align: center; border-collapse: collapse;">
            <tr>
                <td style="width: 50%; padding: 0 20px;">
                    <div style="border-top: 1px solid #000; padding-top: 5px;"><strong>AJA EDUCAÇÃO E ENTRETENIMENTO LTDA</strong><br>CNPJ: 46.422.995/0001-80</div>
                </td>
                <td style="width: 50%; padding: 0 20px;">
                    <div style="border-top: 1px solid #000; padding-top: 5px;"><strong>ASSINATURA DO CONTRATANTE</strong><br>Responsável Financeiro</div>
                </td>
            </tr>
        </table>
    </div>

    <div class="quebra-pagina"></div>
    <div class="titulo-carne">CARNÊ DE PAGAMENTO - JAVIS GAME ACADEMY</div>

    {% for p in parcelas_asaas %}
    <div class="carne-card">
        <div class="carne-header">
            PARCELA {{ p.numero }} DE {{ total_parcelas }}
            <span style="float: right;">Vencimento: {{ p.vencimento }}</span>
        </div>
        <div class="carne-body">
            <div style="display: table; width: 100%;">
                <div style="display: table-cell; width: 70%; vertical-align: top;">
                    <div class="carne-linha"><span class="bold">Aluno:</span> {{ aluno_nome }}</div>
                    <div class="carne-linha"><span class="bold">Beneficiário:</span> JAVIS GAME ACADEMY</div>
                    <div class="carne-linha"><span class="bold">Código de Barras:</span></div>
                    <div style="font-family: monospace; font-size: 8pt; background: #eee; padding: 5px; margin-top: 5px;">
                        {{ p.linha_digitavel }}
                    </div>
                </div>
                <div style="display: table-cell; width: 30%; text-align: center;">
                    <p style="font-size: 7pt; font-weight: bold; margin-bottom: 2px;">PAGAR VIA PIX</p>
                    <img src="{{ p.pix_base64 }}" style="width: 80px; height: 80px; border: 1px solid #000;">
                </div>
            </div>
            <table style="width: 100%; margin-top: 10px;">
                <tr>
                    <td style="font-size: 12pt;"><span class="bold">VALOR:</span> R$ {{ "%.2f"|format(p.valor) }}</td>
                    <td style="text-align: right; font-size: 8pt;">
                        Bônus de Pontualidade: R$ 60,00 se pago até o dia 08
                    </td>
                </tr>
            </table>
        </div>
    </div>
    {% endfor %}

</body>
</html>
"""

# ========================================================
# NOVA ROTA: GERAR CONTRATO PRIVADO + CARNÊ
# ========================================================
@router.post("/gerar-contrato-privado-html")
async def gerar_contrato_privado_endpoint(dados: ContratoData, authorization: str = Header(None)):
    try:
        # 1. VALIDAÇÃO E CONTEXTO DO VENDEDOR
        id_vendedor = None
        id_unidade_vendedor = 1
        if authorization:
            token = authorization.split(" ")[1]
            ctx = get_contexto_usuario(token)
            # Apenas Comercial (3) ou Gerência (8+) podem operar
            if ctx["nivel"] != 3 and ctx["nivel"] < 8:
                raise HTTPException(status_code=403, detail="Acesso restrito.")
            id_vendedor = ctx["id_colaborador"]
            id_unidade_vendedor = ctx["id_unidade"]

        # Limpeza de dados básicos
        nome_oficial_curso = dados.curso.upper()
        horario_limpo = dados.horario_aula if dados.horario_aula else "A definir"

        # 2. INTEGRAÇÃO FINANCEIRA (ASAAS)
        # Identifica quem é o responsável financeiro para o Asaas
        nome_fin = dados.responsavel_nome if dados.responsavel_nome else dados.aluno_nome
        cpf_fin = dados.responsavel_cpf if dados.responsavel_cpf else dados.aluno_cpf
        
        # A. Cria ou busca o cliente no Asaas
        customer_id = criar_ou_buscar_cliente_asaas(nome_fin, cpf_fin, dados.email, dados.whatsapp)

        # B. Gera a Cobrança da ENTRADA (Taxa de Matrícula + Valor de Entrada)
        url_pagamento_entrada = None
        id_asaas_entrada = None
        if dados.valor_entrada and dados.valor_entrada > 0:
            payload_entrada = {
                "customer": customer_id,
                "billingType": "UNDEFINED", # Permite PIX, Boleto ou Cartão
                "value": dados.valor_entrada,
                "dueDate": datetime.now().strftime("%Y-%m-%d"),
                "description": f"Taxa de Matrícula e Entrada - {dados.aluno_nome}",
                "postalService": False
            }
            res_ent = requests.post(f"{ASAAS_URL}/payments", json=payload_entrada, headers=headers_asaas).json()
            url_pagamento_entrada = res_ent.get("invoiceUrl")
            id_asaas_entrada = res_ent.get("id")

        # C. Gera o Parcelamento das MENSALIDADES
        res_mensalidades = gerar_cobranca_parcelada_asaas(customer_id, dados.valor_total, dados.parcelas)
        installment_id = res_mensalidades.get("installment")
        
        # D. Busca detalhes técnicos (PIX/Boleto) de cada parcela para o PDF
        # Note: Isso pode levar alguns segundos devido às múltiplas chamadas ao Asaas
        dados_parcelas = obter_detalhes_parcelas_asaas(installment_id)

        # 3. GERAÇÃO DO PDF (CONTRATO + CARNÊ)
        template = Template(TEMPLATE_HTML_CONTRATO_PRIVADO)
        html_renderizado = template.render(
            curso_oficial=nome_oficial_curso,
            horario_aula=horario_limpo, 
            aluno_nome=dados.aluno_nome,
            aluno_cpf=dados.aluno_cpf,
            aluno_nascimento=dados.aluno_nascimento,
            whatsapp=dados.whatsapp,
            endereco=dados.endereco,
            bairro=dados.bairro,
            cep=dados.cep,
            responsavel_nome=dados.responsavel_nome,
            responsavel_cpf=dados.responsavel_cpf,
            responsavel_rg=dados.responsavel_rg,
            valor_total=dados.valor_total,
            parcelas=dados.parcelas,
            vencimento=8, # Fixo dia 8 conforme regra da escola
            parcelas_asaas=dados_parcelas, # Lista com PIX e Linha Digitável
            total_parcelas=dados.parcelas
        )

        pdf_file = io.BytesIO()
        pisa.CreatePDF(io.StringIO(html_renderizado), dest=pdf_file)
        pdf_bytes = pdf_file.getvalue()

        # Upload para o Bucket exclusivo de contratos privados
        nome_arquivo = f"Contrato_{dados.aluno_nome.replace(' ', '_')}_{int(time.time())}.pdf"
        supabase.storage.from_("privado_contrato").upload(
            nome_arquivo, 
            pdf_bytes, 
            file_options={"content-type": "application/pdf", "upsert": "true"}
        )
        url_pdf = supabase.storage.from_("privado_contrato").get_public_url(nome_arquivo)

        # 4. PERSISTÊNCIA NO BANCO DE DADOS (SUPABASE)
        
        # A. Salva registro do contrato gerado
        supabase.table("tb_contratos_privados").insert({
            "aluno_nome": dados.aluno_nome,
            "aluno_cpf": dados.aluno_cpf,
            "url_pdf": url_pdf,
            "valor_total": dados.valor_total,
            "id_vendedor": id_vendedor,
            "id_unidade": id_unidade_vendedor
        }).execute()

        # B. Cadastra o Aluno
        nasc_formatado = dados.aluno_nascimento.replace("-", "")[:8] if dados.aluno_nascimento else None
        aluno_resp = supabase.table("tb_alunos").insert({
            "nome_completo": dados.aluno_nome.upper(),
            "cpf": dados.aluno_cpf,
            "email": dados.email,
            "celular": dados.whatsapp,
            "data_nascimento": nasc_formatado,
            "id_unidade": id_unidade_vendedor,
            "id_asaas": customer_id # Salva o ID do cliente Asaas para futuras cobranças
        }).execute()

        if aluno_resp.data:
            novo_id_aluno = aluno_resp.data[0]["id_aluno"]
            
            # C. Vincula à Turma
            if dados.turma_codigo:
                supabase.table("tb_matriculas").insert({
                    "id_aluno": novo_id_aluno, 
                    "codigo_turma": dados.turma_codigo, 
                    "id_vendedor": id_vendedor, 
                    "status_financeiro": "Ok"
                }).execute()

            # D. Espelhamento Financeiro Local (Entrada + Mensalidades)
            parcelas_db = []
            
            # Registro da Entrada
            if id_asaas_entrada:
                parcelas_db.append({
                    "id_aluno": novo_id_aluno,
                    "valor": dados.valor_entrada,
                    "data_vencimento": datetime.now().strftime("%Y-%m-%d"),
                    "status": "Pendente",
                    "tipo": "Entrada",
                    "id_asaas_cobranca": id_asaas_entrada
                })

            # Registro das Mensalidades do Carnê
            for p in dados_parcelas:
                parcelas_db.append({
                    "id_aluno": novo_id_aluno,
                    "numero_parcela": p["numero"],
                    "total_parcelas": dados.parcelas,
                    "valor": p["valor"],
                    "data_vencimento": datetime.strptime(p["vencimento"], "%d/%m/%Y").strftime("%Y-%m-%d"),
                    "status": "Pendente",
                    "tipo": "Mensalidade",
                    "id_asaas_cobranca": p["id_asaas"]
                })
            
            if parcelas_db:
                supabase.table("tb_financeiro").insert(parcelas_db).execute()

        # 5. RETORNO PARA O FRONTEND
        return {
            "status": "success", 
            "url_pdf": url_pdf, 
            "url_entrada": url_pagamento_entrada # Link para o vendedor enviar via WhatsApp
        }

    except Exception as e:
        logger.error(f"Erro Crítico na Geração de Contrato Privado: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

def criar_ou_buscar_cliente_asaas(nome, cpf, email, telefone):
    # Limpa CPF para busca
    cpf_limpo = ''.join(filter(str.isdigit, cpf))
    search_url = f"{ASAAS_URL}/customers?cpfCnpj={cpf_limpo}"
    res = requests.get(search_url, headers=headers_asaas).json()
    
    if res.get("data"):
        return res["data"][0]["id"]
    
    payload = {
        "name": nome,
        "cpfCnpj": cpf_limpo,
        "email": email,
        "mobilePhone": telefone
    }
    new_res = requests.post(f"{ASAAS_URL}/customers", json=payload, headers=headers_asaas).json()
    return new_res.get("id")

def gerar_cobranca_parcelada_asaas(customer_id, valor_total, parcelas):
    hoje = datetime.now()
    # Primeiro vencimento sempre no dia 08 do mês seguinte
    proximo_mes = (hoje.replace(day=1) + timedelta(days=32))
    primeiro_vencimento = proximo_mes.replace(day=8)
    
    payload = {
        "customer": customer_id,
        "billingType": "UNDEFINED", # Cliente escolhe como pagar
        "value": valor_total,
        "installmentCount": parcelas,
        "dueDate": primeiro_vencimento.strftime("%Y-%m-%d"),
        "description": "Mensalidades Javis Game Academy"
    }
    
    res = requests.post(f"{ASAAS_URL}/payments", json=payload, headers=headers_asaas).json()
    return res

def obter_detalhes_parcelas_asaas(installment_id):
    # Busca todas as faturas do parcelamento
    url = f"{ASAAS_URL}/payments?installment={installment_id}"
    res = requests.get(url, headers=headers_asaas).json()
    
    parcelas_detalhadas = []
    for payment in res.get("data", []):
        pay_id = payment["id"]
        
        # Busca Linha Digitável do Boleto
        res_boleto = requests.get(f"{ASAAS_URL}/payments/{pay_id}/identificationField", headers=headers_asaas).json()
        
        # Busca QR Code do PIX
        res_pix = requests.get(f"{ASAAS_URL}/payments/{pay_id}/pixQrCode", headers=headers_asaas).json()
        
        parcelas_detalhadas.append({
            "id_asaas": pay_id,
            "numero": payment.get("installmentNumber"),
            "vencimento": datetime.strptime(payment.get("dueDate"), "%Y-%m-%d").strftime("%d/%m/%Y"),
            "valor": payment.get("value"),
            "linha_digitavel": res_boleto.get("identificationField"),
            "pix_base64": f"data:image/png;base64,{res_pix.get('encodedImage')}"
        })
    
    return sorted(parcelas_detalhadas, key=lambda x: x['numero'])

@router.post("/webhook/asaas")
async def webhook_asaas(request: Request):
    payload = await request.json()
    if payload.get("event") not in ["PAYMENT_RECEIVED", "PAYMENT_CONFIRMED"]:
        return {"status": "ignored"}

    id_cobranca = payload['payment']['id']

    # Verifica se esse pagamento é uma entrada de pré-matrícula
    res_pre = supabase.table("tb_pre_matriculas").select("*").eq("id_asaas_entrada", id_cobranca).maybe_single().execute()

    if res_pre.data:
        pre_id = res_pre.data['id']
        dados = res_pre.data['dados_json']
        
        # 1. EFETIVA O ALUNO (tb_alunos)
        aluno_resp = supabase.table("tb_alunos").insert({
            "nome_completo": dados['aluno_nome'].upper(),
            "cpf": dados['aluno_cpf'],
            "email": dados['email'],
            "celular": dados['whatsapp'],
            "id_unidade": res_pre.data['id_unidade'],
            "id_asaas": res_pre.data['id_asaas_cliente']
        }).execute()
        
        novo_id = aluno_resp.data[0]["id_aluno"]

        # 2. GERA AS 12 PARCELAS NO ASAAS (Agora de verdade)
        gerar_cobranca_parcelada_asaas(res_pre.data['id_asaas_cliente'], dados['valor_total'], dados['parcelas'])
        
        # 3. VINCULA À TURMA E ATUALIZA STATUS
        if dados.get("turma_codigo"):
            supabase.table("tb_matriculas").insert({"id_aluno": novo_id, "codigo_turma": dados["turma_codigo"], "id_vendedor": res_pre.data['id_vendedor']}).execute()
        
        supabase.table("tb_pre_matriculas").update({"status": "Pago e Matriculado"}).eq("id", pre_id).execute()

    return {"status": "ok"}

@router.post("/gerar-pre-matricula")
async def gerar_pre_matricula(dados: ContratoData, authorization: str = Header(None)):
    try:
        ctx = obter_dados_token(authorization)
        
        # 1. ASAAS: Criar ou buscar o cliente (precisamos do ID dele)
        nome_fin = dados.responsavel_nome if dados.responsavel_nome else dados.aluno_nome
        cpf_fin = dados.responsavel_cpf if dados.responsavel_cpf else dados.aluno_cpf
        customer_id = criar_ou_buscar_cliente_asaas(nome_fin, cpf_fin, dados.email, dados.whatsapp)

        # 2. PDF: Gerar o contrato (versão preliminar sem o carnê de mensalidades ainda)
        template = Template(TEMPLATE_HTML_CONTRATO_PRIVADO)
        html_renderizado = template.render(
            curso_oficial=dados.curso.upper(),
            horario_aula=dados.horario_aula or "A definir",
            aluno_nome=dados.aluno_nome,
            aluno_cpf=dados.aluno_cpf,
            aluno_nascimento=dados.aluno_nascimento,
            whatsapp=dados.whatsapp,
            endereco=dados.endereco,
            bairro=dados.bairro,
            cep=dados.cep,
            responsavel_nome=dados.responsavel_nome,
            responsavel_cpf=dados.responsavel_cpf,
            responsavel_rg=dados.responsavel_rg,
            valor_total=dados.valor_total,
            parcelas=dados.parcelas,
            parcelas_asaas=[], # Lista vazia: carné sai em branco nesta etapa
            total_parcelas=dados.parcelas
        )

        pdf_file = io.BytesIO()
        pisa.CreatePDF(io.StringIO(html_renderizado), dest=pdf_file)
        
        nome_arquivo = f"Pre_{dados.aluno_nome.replace(' ', '_')}_{int(time.time())}.pdf"
        supabase.storage.from_("privado_contrato").upload(nome_arquivo, pdf_file.getvalue(), {"content-type": "application/pdf"})
        url_pdf = supabase.storage.from_("privado_contrato").get_public_url(nome_arquivo)

        # 3. SUPABASE: Salva na tb_pre_matriculas para aguardar o PIX da taxa
        res = supabase.table("tb_pre_matriculas").insert({
            "dados_json": dados.model_dump(),
            "url_contrato": url_pdf,
            "status": "Aguardando Pagamento",
            "id_asaas_cliente": customer_id,
            "id_vendedor": ctx["id_colaborador"],
            "id_unidade": ctx["id_unidade"]
        }).execute()

        return {"status": "success", "url_pdf": url_pdf, "id_pre": res.data[0]['id']}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/gerar-pagamento-entrada/{id_pre}")
async def gerar_pagamento_entrada(id_pre: str):
    pre = supabase.table("tb_pre_matriculas").select("*").eq("id", id_pre).single().execute()
    if not pre.data: raise HTTPException(status_code=404)
    
    dados = pre.data['dados_json']
    # Gera PIX da entrada (Taxa + Entrada)
    payload = {
        "customer": pre.data['id_asaas_cliente'],
        "billingType": "PIX",
        "value": dados['valor_entrada'],
        "dueDate": datetime.now().strftime("%Y-%m-%d"),
        "description": f"Taxa de Matrícula - {dados['aluno_nome']}"
    }
    res_asaas = requests.post(f"{ASAAS_URL}/payments", json=payload, headers=headers_asaas).json()
    
    # Salva o ID dessa cobrança específica na pré-matrícula
    supabase.table("tb_pre_matriculas").update({"id_asaas_entrada": res_asaas['id']}).eq("id", id_pre).execute()
    
    return {"url_pagamento": res_asaas['invoiceUrl']}


@router.post("/sprints-pedagogicas")
async def upsert_sprint(dados: SprintPedagogicaData):
    data_alvo = dados.data_aula if dados.data_aula else str(date.today())
    
    payload = {
        "data_aula": data_alvo,
        "turma_id": dados.turma_id,
        "professor_name": dados.professor_name,
        "check_chegada_cedo": dados.check_chegada_cedo,
        "check_sala_organizada": dados.check_sala_organizada,
        "check_recepcao_alunos": dados.check_recepcao_alunos,
        "check_foto_grupo_chamada": dados.check_foto_grupo_chamada,
        "check_inicio_horario": dados.check_inicio_horario,
        "check_foto_pais": dados.check_foto_pais,
        "check_chamada_assinada": dados.check_chamada_assinada,
        "check_chamada_site": dados.check_chamada_site,
        "check_ligacao_faltantes": dados.check_ligacao_faltantes,
        "observacoes": dados.observacoes,
        "hora_chegada": dados.hora_chegada,
        "updated_at": "now()"
    }

    try:
        # Usa o cliente do Supabase direto para fazer o Upsert
        # (Assumindo que sua variável do cliente supabase se chama 'supabase')
        response = supabase.table("sprints_pedagogicas").upsert(payload).execute()
        return {"mensagem": "Checklist sincronizado com sucesso!", "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/sprints-pedagogicas/{turma_id}")
async def get_sprint(turma_id: str):
    data_hoje = str(date.today())
    
    try:
        response = supabase.table("sprints_pedagogicas") \
            .select("*") \
            .eq("turma_id", turma_id) \
            .eq("data_aula", data_hoje) \
            .execute()
            
        if not response.data:
            return {} # Retorna vazio se não tiver dados
            
        return response.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
