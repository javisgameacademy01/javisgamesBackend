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
    horario_aula: Optional[str] = "A definir"  # ADICIONADO AQUI
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

LOGO_JAVIS_BASE64 = "data:image/png;base64,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"
IMAGEM_FUNDO_BASE64 = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAfQAAAH0CAIAAABEtEjdAAAACXBIWXMAAA7EAAAOxAGVKw4bAAAFS2lUWHRYTUw6Y29tLmFkb2JlLnhtcAAAAAAAPD94cGFja2V0IGJlZ2luPSfvu78nIGlkPSdXNU0wTXBDZWhpSHpyZVN6TlRjemtjOWQnPz4KPHg6eG1wbWV0YSB4bWxuczp4PSdhZG9iZTpuczptZXRhLyc+CjxyZGY6UkRGIHhtbG5zOnJkZj0naHR0cDovL3d3dy53My5vcmcvMTk5OS8wMi8yMi1yZGYtc3ludGF4LW5zIyc+CgogPHJkZjpEZXNjcmlwdGlvbiByZGY6YWJvdXQ9JycKICB4bWxuczpBdHRyaWI9J2h0dHA6Ly9ucy5hdHRyaWJ1dGlvbi5jb20vYWRzLzEuMC8nPgogIDxBdHRyaWI6QWRzPgogICA8cmRmOlNlcT4KICAgIDxyZGY6bGkgcmRmOnBhcnNlVHlwZT0nUmVzb3VyY2UnPgogICAgIDxBdHRyaWI6Q3JlYXRlZD4yMDI2LTAzLTE2PC9BdHRyaWI6Q3JlYXRlZD4KICAgICA8QXR0cmliOkRhdGE+eyZxdW90O2RvYyZxdW90OzomcXVvdDtEQUhFSnZYaGEyTSZxdW90OywmcXVvdDt1c2VyJnF1b3Q7OiZxdW90O1VBRUNGQVdJSUJjJnF1b3Q7LCZxdW90O2JyYW5kJnF1b3Q7OiZxdW90O0JBRUNGT0ZPcnVRJnF1b3Q7fTwvQXR0cmliOkRhdGE+CiAgICAgPEF0dHJpYjpFeHRJZD4yNTFjOTJlNy1mNGU0LTQ4YzctOTdhNy00MGMyNWRkOTczZTU8L0F0dHJpYjpFeHRJZD4KICAgICA8QXR0cmliOkZiSWQ+NTI1MjY1OTE0MTc5NTgwPC9BdHRyaWI6RmJJZD4KICAgICA8QXR0cmliOlRvdWNoVHlwZT4yPC9BdHRyaWI6VG91Y2hUeXBlPgogICAgPC9yZGY6bGk+CiAgIDwvcmRmOlNlcT4KICA8L0F0dHJpYjpBZHM+CiA8L3JkZjpEZXNjcmlwdGlvbj4KCiA8cmRmOkRlc2NyaXB0aW9uIHJkZjphYm91dD0nJwogIHhtbG5zOmRjPSdodHRwOi8vcHVybC5vcmcvZGMvZWxlbWVudHMvMS4x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"

TEMPLATE_HTML_CONTRATO = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <style>
        /* Estilo para a imagem de fundo */
        #background {
            position: absolute;
            top: 250px;     /* Ajusta a altura da logo no fundo */
            left: 100px;    /* Ajusta a posição lateral */
            width: 450px;   /* Largura da logo de fundo */
            z-index: -1000; /* Garante que fica atrás do texto */
            opacity: 0.1;   /* Deixa a imagem bem clarinha (marca d'água) */
        }
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
        .logo-img {
            max-width: 300px; /* Aumente para 300px ou diminua para 200px conforme preferir */
            height: auto;     /* Isso mantém a proporção correta para não amassar a imagem */
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
        .tabela-dados { 
            width: 100%; 
            border-collapse: collapse; 
            margin-bottom: 15px; 
        }
        .tabela-dados td { 
            padding: 3px 0; 
            vertical-align: bottom; 
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
    <div id="background">
        <img src="{{ imagem_fundo }}" style="width: 100%;">
    </div>

    <div class="header">
        <img src="{{ logo_javis }}" class="logo-img">
    </div>

    <div class="titulo">
        Termo de Compromisso do Aluno
    </div>

    <div class="texto-justificado">
        Pelo presente instrumento particular, as partes a seguir qualificadas:<br>
        Por meios do <strong>INSTITUTO DO DESENVOLVIMENTO ECONÔMICO, TECNOLÓGICO E CULTURA - IDEC</strong>, sob CNPJ 19.136.591/0001-57, contratando a empresa abaixo para execução do projeto.<br>
        De um lado, <strong>PROJETO DE {{ curso_oficial }}</strong> pessoa jurídica de direito privado, inscrita no CNPJ sob o nº 46.422.995/0001-80 com sede em Av. Historiador Rubens de Mendonça, 1593, Bosque da Saúde - CEP 78050-000- Cuiabá/MT, doravante denominada <strong>JAVIS GAME ACADEMY</strong>.
    </div>

    <table class="tabela-dados">
        <tr>
            <td colspan="2"><span class="bold">Aluno:</span> {{ aluno_nome }}</td>
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
            <td style="width: 50%;"><span class="bold">Nome da Escola:</span> {{ escola_nome }}</td>
            <td style="width: 25%;"><span class="bold">Turno:</span> {{ escola_turno }}</td>
            <td style="width: 25%;"><span class="bold">Série:</span> {{ escola_serie }}</td>
        </tr>
        {% if responsavel_nome %}
        <tr>
            <td colspan="3" style="padding-top: 8px;"><span class="bold">Responsável Legal:</span> {{ responsavel_nome }}</td>
        </tr>
        <tr>
            <td colspan="2">
                <span class="bold">CPF:</span> {{ responsavel_cpf }} 
                {% if responsavel_rg %} | <span class="bold">RG:</span> {{ responsavel_rg }} {{ responsavel_rg_orgao }} {% endif %}
            </td>
            <td><span class="bold">Grau Parentesco:</span> {{ responsavel_parentesco }}</td>
        </tr>
        {% endif %}
    </table>

    <div class="texto-justificado">
        Resolvem, de comum acordo, celebrar le presente Termo de Compromisso, mediante as cláusulas e condições seguintes:
    </div>

    <div class="clausula-titulo">Cláusula Primeira - Do Objeto</div>
    <div class="texto-justificado">
        O presente Termo tem como objeto a concessão de uma bolsa de estudo integral e gratuita para o(a) ALUNO(A) no curso de <strong>{{ curso_oficial }}</strong> 
        {% if curso == 'GAME DEV' %}
            com 60h de duração e 6 (seis) meses, 
        {% else %}
            com duração de 3 (três) meses, 
        {% endif %}
        promovido pelo PROJETO SOCIAL.
    </div>

    <div class="clausula-titulo">Cláusula Segunda - Das Condições do Curso</div>
    <div class="texto-justificado">
        1. O curso será ministrado de _____/____/_______ a _____/____/_______, 
        {% if curso == 'GAME DEV' %}
            com carga horária de 60 horas, distribuídas em 24 aulas.<br>
        {% else %}
            com carga horária de 30 horas, distribuídas em 12 aulas.<br>
        {% endif %}
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
        O PROJETO SOCIAL poderá rescindir o Termo de imediato, sem prévio aviso, em caso de descumprimento grave de qualquer das obrigações assumidas pelo (a) ALUNO(A) na Cláusula Terceira, como por exemplo, mas não se limitando a:
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
        <div class="texto-justificado">
            E, por estarem assim justos e contratados, as partes assinam o presente Termo de Compromisso em 2 (duas) vias de igual teor e forma, na presença das 2 (duas) testemunhas abaixo, para que produza seus devidos efeitos legais.
        </div>

        <div class="data-local">
            Cuiabá - MT, ______ de __________________________ de 2026.
        </div>

        <table class="tabela-assinaturas">
            <tr>
                <td>
                    <div class="linha-assinatura"></div>
                    <strong>ALUNO(A) / RESPONSÁVEL LEGAL</strong>
                </td>
                <td>
                    <div class="linha-assinatura"></div>
                    <strong>PROJETO CURSO DE<br>{{ curso_oficial }}</strong>
                </td>
            </tr>
            <tr>
                <td>
                    <div class="linha-assinatura"></div>
                    <strong>Testemunhas 1</strong><br>
                    RG: / CPF:
                </td>
                <td>
                    <div class="linha-assinatura"></div>
                    <strong>Testemunhas 2</strong><br>
                    RG: / CPF:
                </td>
            </tr>
        </table>
    </div>

</body>
</html>
"""

@router.post("/gerar-contrato-html")
async def gerar_contrato_endpoint(dados: ContratoData, authorization: str = Header(None)):
    try:
        # 1. Define o nome oficial para o cabeçalho do contrato
        nome_oficial_curso = "EMPREENDEDORISMO DIGITAL" if dados.curso == "PERFORMANCE GAMER" else dados.curso

        # 2. LÓGICA DE TRATAMENTO DO HORÁRIO:
        # Se vier vazio ou um valor padrão, limpamos para "A combinar" para disparar a linha ______ no template
        horario_limpo = dados.horario_aula
        if not horario_limpo or horario_limpo in ["A definir", "A combinar", "A combinar com a coordenação"]:
            horario_limpo = "A combinar"

        # 3. Renderiza o HTML com o template integral
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
            responsavel_rg_orgao=dados.responsavel_rg_orgao,
            imagem_fundo=IMAGEM_FUNDO_BASE64,
            logo_javis=LOGO_JAVIS_BASE64
        )

        # 4. Gera o PDF
        pdf_file = io.BytesIO()
        pisa.CreatePDF(io.StringIO(html_renderizado), dest=pdf_file)
        pdf_bytes = pdf_file.getvalue()

        # 5. Faz upload para o Supabase Storage
        nome_arquivo = f"Contrato_{dados.aluno_nome.replace(' ', '_')}_{int(time.time())}.pdf"
        supabase.storage.from_("termos").upload(
            nome_arquivo, 
            pdf_bytes, 
            file_options={"content-type": "application/pdf", "upsert": "true"}
        )

        # 6. Pega a URL pública
        url_pdf = supabase.storage.from_("termos").get_public_url(nome_arquivo)

        # 7. Salva o registro completo na tabela tb_geracao_termos
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
    """ Rota administrativa para corrigir PDFs antigos em massa com a lógica de horários """
    try:
        # Busca todos os contratos já cadastrados
        contratos = supabase.table("tb_geracao_termos").select("*").execute().data
        if not contratos: 
            return {"message": "Nenhum registo encontrado."}

        atualizados = 0
        template = Template(TEMPLATE_HTML_CONTRATO)

        for d in contratos:
            # Tradução do curso
            c_nome = d.get("curso", "")
            oficial = "EMPREENDEDORISMO DIGITAL" if c_nome == "PERFORMANCE GAMER" else c_nome
            
            # --- O BLOCO QUE VOCÊ PEDIU (Lógica de Limpeza de Horário) ---
            horario_aluno = d.get("horario_aula")
            if not horario_aluno or horario_aluno in ["A definir", "A combinar", "A combinar com a coordenação"]:
                horario_aluno = "A combinar" # Isto dispara a linha _________________ no PDF
            # -------------------------------------------------------------

            # Renderiza o PDF para o aluno atual
            html = template.render(
                curso=c_nome,
                curso_oficial=oficial,
                horario_aula=horario_aluno, # Aplica o horário tratado aqui
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
                responsavel_rg_orgao=d.get("responsavel_rg_orgao"),
                imagem_fundo=IMAGEM_FUNDO_BASE64,
                logo_javis=LOGO_JAVIS_BASE64
            )

            # Processo técnico de criação do arquivo
            pdf_io = io.BytesIO()
            pisa.CreatePDF(io.StringIO(html), dest=pdf_io)
            
            # Nome único para o novo arquivo
            f_nome = f"Refeito_{d['id']}_{int(time.time())}.pdf"
            supabase.storage.from_("termos").upload(
                f_nome, 
                pdf_io.getvalue(), 
                file_options={"content-type": "application/pdf"}
            )
            
            # Atualiza o link do PDF na linha do aluno no banco de dados
            nova_url = supabase.storage.from_("termos").get_public_url(f_nome)
            supabase.table("tb_geracao_termos").update({"url_pdf": nova_url}).eq("id", d["id"]).execute()
            
            atualizados += 1
            # Pausa curta para não sobrecarregar o servidor do Render
            time.sleep(0.3)

        return {"status": "success", "message": f"{atualizados} contratos regenerados com sucesso com a nova lógica visual!"}

    except Exception as e:
        logger.error(f"Erro na regeneração em massa: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
