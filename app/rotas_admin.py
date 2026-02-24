"""
Rotas administrativas do sistema
"""
import os
from fastapi import APIRouter, HTTPException, Header, UploadFile, File, Form, Query
from pydantic import BaseModel
from supabase import create_client, Client
from datetime import datetime, timedelta
import requests
import logging
from typing import Optional
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
    MensagemDiretaData, # <--- Verifique se está aqui
    MensagemGrupoData   # <--- Verifique se está aqui
)
from app.modelos import (
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
    LoginData,
    NovoAlunoData,
    TurmaData,
    NovoUsuarioData

)

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


def get_contexto_usuario(token: str):
    try:
        user = supabase.auth.get_user(token)
        user_id = user.user.id
        
        resp = supabase.table("tb_colaboradores")\
            .select("id_colaborador, id_unidade, id_cargo, tb_cargos!fk_cargos(nivel_acesso)")\
            .eq("user_id", user_id)\
            .single()\
            .execute()
            
        dados = resp.data
        return {
            "user_id": user_id,
            "id_colaborador": dados['id_colaborador'],
            "id_unidade": dados['id_unidade'],
            "id_cargo": dados['id_cargo'],
            "nivel": dados['tb_cargos']['nivel_acesso']
        }
    except Exception as e:
        print(f"Erro contexto usuario: {e}")
        raise HTTPException(status_code=401, detail="Usuário não identificado.")


# --- ROTAS ---
# As rotas serão adicionadas abaixo
# =========================================
# AULAS EXPERIMENTAIS
# Tabela: tb_aulas_experimentais
# Colunas (como você pediu):
# ID, RESPONSÁVEL, CONTATO1, CONTATO2, ALUNO(A),
# DATA DA AULA, HORÁRIO, CURSO, ORIGEM, VENDEDOR (FK),
# STATUS DE ATENDIMENTO, OBSERVAÇÃO, DATACRIAÇÃO
# =========================================

class AulaExperimentalCreate(BaseModel):
    responsavel: Optional[str] = None
    contato1: Optional[str] = None
    contato2: Optional[str] = None
    aluno: str
    data_aula: str  # "YYYY-MM-DD"
    horario: Optional[str] = None
    curso: Optional[str] = None
    origem: Optional[str] = None
    id_vendedor: Optional[int] = None  # FK tb_colaboradores.id_colaborador
    status_atendimento: Optional[str] = None
    observacao: Optional[str] = None


class AulaExperimentalUpdate(BaseModel):
    responsavel: Optional[str] = None
    contato1: Optional[str] = None
    contato2: Optional[str] = None
    aluno: Optional[str] = None
    data_aula: Optional[str] = None
    horario: Optional[str] = None
    curso: Optional[str] = None
    origem: Optional[str] = None
    id_vendedor: Optional[int] = None
    status_atendimento: Optional[str] = None
    observacao: Optional[str] = None


def _pode_editar_aula_experimental(ctx: dict) -> bool:
    # Vendedor (3) e Coordenação/Gerência (>=8)
    return (ctx.get("nivel") == 3) or (ctx.get("nivel", 0) >= 8)


# =========================================
# AULAS EXPERIMENTAIS CORRIGIDAS
# =========================================

@router.get("/aulas-experimentais")
def listar_aulas_experimentais(
    q: Optional[str] = None,
    data: Optional[str] = None,
    authorization: str = Header(None)
):
    if not authorization:
        raise HTTPException(status_code=401)

    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)

    try:
        query = supabase.table("tb_aulas_experimentais").select("*, tb_colaboradores(nome_completo)")

        # Filtro de unidade para Nível 8
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
        # CAPTURAMOS O ERRO REAL DO SUPABASE!
        erro_banco = str(e)
        logger.error(f"Erro Supabase: {erro_banco}")
        
        # Traduzimos o erro para você ver na tela o que precisa arrumar no banco:
        if "id_unidade" in erro_banco:
            raise HTTPException(status_code=500, detail="Falta a coluna 'id_unidade' na tabela tb_aulas_experimentais no Supabase.")
        elif "tb_colaboradores" in erro_banco or "relationship" in erro_banco:
            raise HTTPException(status_code=500, detail="Falta a Chave Estrangeira (Foreign Key) em 'id_vendedor' ligando a tb_colaboradores.")
        elif "does not exist" in erro_banco and "tb_aulas_experimentais" in erro_banco:
            raise HTTPException(status_code=500, detail="A tabela 'tb_aulas_experimentais' não existe no banco de dados.")
            
        raise HTTPException(status_code=500, detail=f"Erro no banco: {erro_banco}")

@router.post("/aulas-experimentais")
def criar_aula_experimental(dados: AulaExperimentalCreate, authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401)

    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)

    if not _pode_editar_aula_experimental(ctx):
        raise HTTPException(status_code=403, detail="Acesso restrito.")

    try:
        payload = dados.model_dump(exclude_none=True)
        if not payload.get("id_vendedor"):
            payload["id_vendedor"] = ctx["id_colaborador"]
        
        # IMPORTANTE: Garante que a unidade seja a do coordenador
        payload["id_unidade"] = ctx["id_unidade"]

        # Mudança de 'db' para 'supabase'
        resp = supabase.table("tb_aulas_experimentais").insert(payload).execute()
        return resp.data[0] if resp.data else {"message": "ok"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.patch("/aulas-experimentais/{id_aula}")
def editar_aula_experimental(id_aula: str, dados: AulaExperimentalUpdate, authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401)

    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)

    if not _pode_editar_aula_experimental(ctx):
        raise HTTPException(status_code=403, detail="Acesso restrito.")
        
    registro = supabase.table("tb_aulas_experimentais").select("id_unidade").eq("id", id_aula).single().execute()
    if not registro.data:
        raise HTTPException(status_code=404, detail="Aula não encontrada.")
    
    if ctx["nivel"] < 9 and registro.data.get("id_unidade") != ctx["id_unidade"]:
        raise HTTPException(status_code=403, detail="Sem permissão para outra unidade.")

    try:
        updates = dados.model_dump(exclude_none=True)
        # Mudança de 'db' para 'supabase'
        supabase.table("tb_aulas_experimentais").update(updates).eq("id", id_aula).execute()
        return {"message": "Atualizado!"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/aulas-experimentais/{id_aula}")
def deletar_aula_experimental(id_aula: str, authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401)

    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)

    if not _pode_editar_aula_experimental(ctx):
        raise HTTPException(status_code=403, detail="Acesso restrito.")

    registro = supabase.table("tb_aulas_experimentais").select("id_unidade").eq("id", id_aula).single().execute()
    if not registro.data:
        raise HTTPException(status_code=404, detail="Aula não encontrada.")
    
    if ctx["nivel"] < 9 and registro.data.get("id_unidade") != ctx["id_unidade"]:
        raise HTTPException(status_code=403, detail="Sem permissão para outra unidade.")

    try:
        # Mudança de 'db' para 'supabase'
        supabase.table("tb_aulas_experimentais").delete().eq("id", id_aula).execute()
        return {"message": "Excluído!"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# === ROTAS ADMINISTRATIVAS ===

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

    # Apenas Nível 8+ (Gerente) pode ver a lista
    if ctx['nivel'] < 8:
        raise HTTPException(status_code=403, detail="Acesso restrito à Gerência.")

    try:
        query = supabase.table("tb_colaboradores")\
            .select("*, tb_cargos!fk_cargos(nome_cargo, nivel_acesso)")\
            .order("nome_completo")
        
        # LÓGICA DO FILTRO:
        if ctx['nivel'] < 9: 
            # Se for Gerente (8) ou menor, FORÇA a ver só a própria unidade
            query = query.eq("id_unidade", ctx['id_unidade'])
        else:
            # Se for Diretor (9 ou 10) e escolheu uma cidade, filtra por ela
            if filtro_unidade:
                query = query.eq("id_unidade", filtro_unidade)
            
        return query.execute().data
    except Exception as e:
        print(f"Erro listar equipe: {e}")
        return []
        

@router.post("/cadastrar-funcionario")
def admin_cadastrar_funcionario(dados: NovoFuncionarioData, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)

    # Apenas Nível 8+ pode cadastrar
    if ctx['nivel'] < 8:
        raise HTTPException(status_code=403, detail="Acesso restrito à Gerência.")

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

    # Apenas Gerentes (8+) podem editar
    if ctx['nivel'] < 8:
        raise HTTPException(status_code=403, detail="Acesso restrito à Gerência.")

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

# 2. GESTÃO DE TURMAS

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
            "tipo_turma": dados.tipo, # Salva se é PROJETO ou PARTICULAR
            "data_inicio": dados.data_inicio,
            "qtd_aulas": dados.qtd_aulas,
            "previsao_termino": previsao,
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
            "tipo_turma": dados.tipo, # Atualiza o tipo
            "data_inicio": dados.data_inicio,
            "qtd_aulas": dados.qtd_aulas,
            "previsao_termino": previsao,
            "data_termino_real": dados.data_termino_real
        }).eq("codigo_turma", codigo_original).execute()
        return {"message": "Turma atualizada!"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# 3. DADOS DO FUNCIONÁRIO

@router.get("/meus-dados")
def get_dados_funcionario(authorization: str = Header(None)):
    if not authorization: 
        raise HTTPException(status_code=401, detail="Token ausente")
    
    try:
        # Extração do Token e User ID
        token = authorization.split(" ")[1]
        user = supabase.auth.get_user(token)
        user_id = user.user.id 
        
        # CONSULTA SIMPLIFICADA:
        # 1. Removemos o !fk_cargos para deixar o Supabase resolver o join automaticamente
        # 2. Removemos o filtro de 'ativo' para evitar bloqueios por dados migrados incorretamente
        response = supabase.table("tb_colaboradores")\
            .select("id_colaborador, nome_completo, telefone, email, id_cargo, id_unidade, tb_cargos(nome_cargo, nivel_acesso)")\
            .eq("user_id", user_id)\
            .execute()
            
        # Se não encontrar o registro na tb_colaboradores
        if not response.data:
            logger.error(f"Acesso negado: User ID {user_id} não encontrado na tb_colaboradores.")
            raise HTTPException(status_code=403, detail="Utilizador não vinculado como colaborador no banco de dados.")
            
        funcionario = response.data[0]
        
        # TRATAMENTO DE SEGURANÇA PARA O CARGO:
        # Se o join com tb_cargos falhar ou o cargo não existir, define valores padrão para não quebrar o front
        cargo_data = funcionario.get('tb_cargos')
        
        # Caso o retorno do join venha como lista (comum em algumas versões do SDK) ou dicionário
        if isinstance(cargo_data, list) and len(cargo_data) > 0:
            cargo_info = cargo_data[0]
        elif isinstance(cargo_data, dict):
            cargo_info = cargo_data
        else:
            cargo_info = {"nome_cargo": "Colaborador", "nivel_acesso": 1}

        # RETORNO PARA O FRONT-END
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
        # Retornamos 403 para que o front entenda que deve tratar o acesso, 
        # mas com o detalhe do erro para saberes o que aconteceu
        raise HTTPException(status_code=403, detail=f"Erro interno de permissão: {str(e)}")
        
@router.get("/conteudo-didatico/cursos")
def admin_listar_cursos_didaticos(authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    try:
        # Busca cursos com módulos e aulas aninhados usando a sintaxe do PostgREST (Supabase)
        resp = supabase.table("cursos")\
            .select("*, modulos(*, aulas(*))")\
            .order("ordem")\
            .execute()
            
        if not resp.data:
            return []
            
        # Garante que a ordenação das aulas dentro dos módulos seja respeitada
        for curso in resp.data:
            for modulo in curso.get('modulos', []):
                modulo['aulas'] = sorted(modulo.get('aulas', []), key=lambda x: x.get('ordem', 0))
                
        return resp.data
    except Exception as e:
        print(f"Erro ao listar cursos didáticos: {e}")
        return []

@router.get("/meus-cursos-permitidos")
def get_cursos_permitidos(authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    try:
        token = authorization.split(" ")[1]
        user = supabase.auth.get_user(token)
        user_id = user.user.id
        
        # 1. Buscar o aluno pelo user_id
        aluno_resp = supabase.table("tb_alunos").select("id_aluno").eq("user_id", user_id).execute()
        if not aluno_resp.data:
            # Se não encontrar aluno, retorna uma lista vazia ou padrão para teste
            return {"cursos": []}
            
        # 2. Por enquanto, como a tabela tb_matriculas está vazia, 
        # vamos retornar todos os cursos ativos como permitidos para não bloquear o aluno.
        # No futuro, aqui deve ser feita a filtragem por matrícula real.
        cursos_resp = supabase.table("cursos").select("id, titulo").eq("ativo", True).execute()
        
        # Mapear para o formato que o frontend espera (slugs)
        mapa_slugs = {
            "GAME PRO": "game-pro",
            "DESIGNER START": "designer-start",
            "GAME DEV": "game-dev",
        }
        
        cursos_permitidos = []
        for c in cursos_resp.data:
            slug = mapa_slugs.get(c['titulo'].upper(), c['titulo'].lower().replace(" ", "-"))
            cursos_permitidos.append({"id": slug, "data_inicio": "2024-01-01"})
            
        return {"cursos": cursos_permitidos}
    except Exception as e:
        print(f"Erro cursos permitidos: {e}")
        return {"cursos": []}

@router.get("/conteudo-aula")
def get_conteudo_aula(titulo: str):
    try:
        # Busca a aula pelo título exato ou aproximado na tabela 'aulas'
        # Usamos .ilike para ignorar diferenças entre maiúsculas/minúsculas
        res = supabase.table("aulas").select("*").ilike("titulo", f"%{titulo}%").execute()
        
        if not res.data:
            return {
                "titulo": titulo,
                "script": "Conteúdo em breve.",
                "codigo_exemplo": "# O código será adicionado em breve.",
                "desafio": "Aguarde o desafio desta aula."
            }
            
        aula = res.data[0]
        conteudo_raw = aula.get('conteudo') or ""
        
        # Lógica de extração das tags [SCRIPT], [CODIGO], [DESAFIO]
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
        
        return {
            "titulo": aula['titulo'],
            "script": script,
            "codigo_exemplo": codigo,
            "desafio": desafio
        }
    except Exception as e:
        print(f"Erro ao buscar conteúdo: {e}")
        return {"titulo": titulo, "script": "Erro ao carregar conteúdo."}
        
# 4. CADASTRO DE ALUNO

@router.post("/cadastrar-aluno")
def admin_cadastrar_aluno(dados: NovoAlunoData, authorization: str = Header(None)):
    # Validação do header
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token ausente")

    token = authorization.split(" ", 1)[1]
    ctx = get_contexto_usuario(token)

    # Permissão (alinha com seu front: menu-cadastro só aparece no 8+)
    # Permissão: Vendedor (3) e Gerência+ (8+)
    if ctx["nivel"] != 3 and ctx["nivel"] < 8:
        raise HTTPException(status_code=403, detail="Acesso restrito.")

    new_user_id = None
    novo_id_aluno = None

    try:
        # 1) Cria o usuário no Supabase Auth
        user_auth = supabase.auth.admin.create_user({
            "email": dados.email,
            "password": dados.senha,
            "email_confirm": True
        })
        new_user_id = user_auth.user.id

        # 2) Formata nascimento (se você usa YYYYMMDD no banco)
        nasc_formatado = None
        if dados.data_nascimento:
            nasc_formatado = dados.data_nascimento.replace("-", "")[:8]

        # 3) Cria aluno (tb_alunos)
        aluno_resp = supabase.table("tb_alunos").insert({
            "nome_completo": dados.nome,
            "cpf": dados.cpf,
            "email": dados.email,              # precisa existir a coluna
            "celular": dados.celular,
            "telefone": dados.telefone,
            "data_nascimento": nasc_formatado,
            "user_id": new_user_id,
            "id_unidade": ctx["id_unidade"],
        }).execute()

        if not aluno_resp.data:
            raise Exception("Falha ao inserir aluno em tb_alunos.")

        novo_id_aluno = aluno_resp.data[0]["id_aluno"]

        # 4) Cria matrícula (tb_matriculas)
        mat_resp = supabase.table("tb_matriculas").insert({
            "id_aluno": novo_id_aluno,
            "codigo_turma": dados.turma_codigo,
            "id_vendedor": ctx["id_colaborador"],
            "status_financeiro": "Ok"
        }).execute()

        if not mat_resp.data:
            raise Exception("Falha ao inserir matrícula em tb_matriculas.")

        return {"message": "Sucesso!", "id_aluno": novo_id_aluno, "user_id": new_user_id}

    except Exception as e:
        # Cleanup do que foi criado (rollback)
        # Se inseriu aluno mas não completou, remove aluno
        if novo_id_aluno:
            try:
                supabase.table("tb_alunos").delete().eq("id_aluno", novo_id_aluno).execute()
            except Exception:
                pass

        # Se criou usuário no Auth, remove usuário
        if new_user_id:
            try:
                supabase.auth.admin.delete_user(new_user_id)  # :contentReference[oaicite:1]{index=1}
            except Exception:
                pass

        raise HTTPException(status_code=400, detail=f"Erro cadastro: {str(e)}")


@router.get("/listar-alunos")
def admin_listar_alunos(authorization: str = Header(None)):
    if not authorization: 
        raise HTTPException(status_code=401, detail="Token ausente")
    
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)
    
    try:
        # 1. Voltamos para o select "*" simples que você sabe que funciona
        # 2. Mantemos os joins, mas sem tentar renomear colunas dentro da query
        query = supabase.table("tb_alunos").select(
            "*, tb_matriculas(id_matricula, codigo_turma, status_financeiro, tb_turmas(tipo_turma, dia_semana))"
        )
        
        # Filtro de Unidade
        if ctx['nivel'] < 9: 
            query = query.eq("id_unidade", ctx['id_unidade'])
            
        res = query.execute()
        
        # TRATAMENTO DE DADOS NO PYTHON (Mais seguro que no SQL)
        # Aqui garantimos que se o campo for 'email_aluno', ele também seja entregue como 'email'
        dados = res.data
        for aluno in dados:
            if 'email_aluno' in aluno and not aluno.get('email'):
                aluno['email'] = aluno['email_aluno']
        
        return dados

    except Exception as e: 
        logger.error(f"Erro ao listar alunos: {e}")
        # Fallback total para não travar o front
        try:
            res_fallback = supabase.table("tb_alunos").select("*").execute()
            return res_fallback.data
        except:
            return []

# 5. REPOSIÇÕES E AGENDA

@router.delete("/reposicao/{id_repo}")
def deletar_reposicao(id_repo: str, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)

    # Verifica permissão (Nível 8+ ou Criador)
    if not verificar_permissao_repo(id_repo, ctx):
        raise HTTPException(status_code=403, detail="Você não tem permissão para excluir esta reposição.")

    try:
        supabase.table("tb_reposicoes").delete().eq("id", id_repo).execute()
        return {"message": "Reposição excluída com sucesso."}
    except Exception as e:
        print(f"Erro delete repo: {e}")
        raise HTTPException(status_code=500, detail="Erro ao excluir.")
        
    return False

@router.post("/agendar-reposicao")
def admin_reposicao(dados: ReposicaoData, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    try:
        token = authorization.split(" ")[1]
        user = supabase.auth.get_user(token)
        user_id = user.user.id

        dt_repo_inicio = datetime.strptime(dados.data_hora, "%Y-%m-%dT%H:%M")
        dt_repo_fim = dt_repo_inicio + timedelta(hours=1) 

        resp_turmas = supabase.table("tb_turmas").select("*").eq("id_professor", dados.id_professor).in_("status", ["Em Andamento", "Planejada"]).execute()

        for turma in resp_turmas.data:
            if not turma['data_inicio'] or not turma['qtd_aulas'] or not turma['horario']: continue
            dt_inicio_turma = datetime.strptime(turma['data_inicio'], "%Y-%m-%d")
            dia_alvo = DIAS_MAPA.get(turma['dia_semana'].split("-")[0].strip(), 0)
            dias_diff = (dia_alvo - dt_inicio_turma.weekday() + 7) % 7
            dt_aula_atual = dt_inicio_turma + timedelta(days=dias_diff)
            hora_h, hora_m = map(int, turma['horario'].split("-")[0].strip().split(":"))

            for _ in range(turma['qtd_aulas']):
                inicio_aula = dt_aula_atual.replace(hour=hora_h, minute=hora_m)
                fim_aula = inicio_aula + timedelta(hours=2, minutes=30)
                if (dt_repo_inicio < fim_aula) and (dt_repo_fim > inicio_aula):
                    raise HTTPException(status_code=409, detail=f"Conflito de horário com turma {turma['codigo_turma']}.")
                dt_aula_atual += timedelta(days=7)

        supabase.table("tb_reposicoes").insert({
            "id_aluno": dados.id_aluno,
            "data_reposicao": dados.data_hora,
            "codigo_turma": dados.turma_codigo,
            "id_professor": dados.id_professor,
            "conteudo_aula": dados.conteudo_aula,
            "motivo": dados.motivo,
            "observacoes": dados.observacoes,
            "criado_por": user_id,
            "status": "Agendada",
            "presenca": None 
        }).execute()
        
        return {"message": "Agendada com sucesso!"}
    except HTTPException as he: raise he
    except Exception as e: raise HTTPException(status_code=400, detail="Erro interno.")
        

@router.get("/agenda-geral")
def admin_agenda(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token ausente")

    token = authorization.split(" ", 1)[1]
    ctx = get_contexto_usuario(token)

    try:
        eventos = []

        # 1) BUSCA REPOSIÇÕES (único tipo que ficará na agenda)
        if ctx["nivel"] < 9:
            alunos = (
                supabase.table("tb_alunos")
                .select("id_aluno")
                .eq("id_unidade", ctx["id_unidade"])
                .execute()
            )
            ids = [a["id_aluno"] for a in (alunos.data or [])]

            resp_repo = None
            if ids:
                resp_repo = (
                    supabase.table("tb_reposicoes")
                    .select("*, tb_alunos(nome_completo), tb_colaboradores(nome_completo)")
                    .in_("id_aluno", ids)
                    .execute()
                )
        else:
            resp_repo = (
                supabase.table("tb_reposicoes")
                .select("*, tb_alunos(nome_completo), tb_colaboradores(nome_completo)")
                .execute()
            )

        if resp_repo and resp_repo.data:
            for rep in resp_repo.data:
                nome_aluno = "Aluno?"
                if rep.get("tb_alunos"):
                    nome_aluno = rep["tb_alunos"].get("nome_completo", "Aluno?")

                nome_prof = "?"
                if rep.get("tb_colaboradores"):
                    nome_prof = rep["tb_colaboradores"].get("nome_completo", "?")

                eventos.append({
                    "id": rep["id"],
                    "title": f"🔄 Reposição: {nome_aluno}",
                    "start": rep["data_reposicao"],
                    "color": "#ff4d4d",
                    "tipo": "reposicao",
                    "nome_aluno": nome_aluno,
                    "nome_prof": nome_prof,
                    "conteudo": rep.get("conteudo_aula"),
                    "turma": rep.get("codigo_turma"),
                    "presenca": rep.get("presenca"),
                    "observacoes": rep.get("observacoes"),
                    "arquivo": rep.get("arquivo_assinatura"),
                    "extendedProps": {
                        "conteudo": rep.get("conteudo_aula"),
                        "id_criador": rep.get("criado_por")
                    }
                })

        # ✅ REMOVIDO: eventos de "aula" das turmas (datas das turmas)

        return eventos

    except Exception as e:
        print(f"Erro agenda: {e}")
        return []



@router.put("/reposicao-completa/{id_repo}")
def atualizar_reposicao_completa(id_repo: str, presenca: str = Form(...), observacoes: str = Form(None), arquivo: UploadFile = File(None), authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    try:
        presenca_bool = None
        if presenca == "true": presenca_bool = True
        elif presenca == "false": presenca_bool = False
        updates = { "presenca": presenca_bool, "observacoes": observacoes }

        if arquivo:
            file_content = arquivo.file.read()
            file_ext = arquivo.filename.split('.')[-1]
            file_path = f"assinatura_{id_repo}.{file_ext}" 
            supabase.storage.from_("listas-chamada").upload(file_path, file_content, file_options={"content-type": arquivo.content_type, "upsert": "true"})
            updates["arquivo_assinatura"] = supabase.storage.from_("listas-chamada").get_public_url(file_path)

        supabase.table("tb_reposicoes").update(updates).eq("id", id_repo).execute()
        return {"message": "Atualizado!"}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))


@router.patch("/editar-reposicao/{id_repo}")
def atualizar_dados_reposicao(id_repo: str, dados: ReposicaoEdicaoData, authorization: str = Header(None)):
    # CORREÇÃO: Usamos ReposicaoEdicaoData para não exigir todos os campos (aluno, prof, etc)
    if not authorization: raise HTTPException(status_code=401)
    
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)

    if not verificar_permissao_repo(id_repo, ctx):
        raise HTTPException(status_code=403, detail="Sem permissão para editar.")

    try:
        updates = {}
        # Só adiciona no update se o dado foi enviado
        if dados.data_hora:
            updates["data_reposicao"] = dados.data_hora
        if dados.conteudo_aula:
            updates["conteudo_aula"] = dados.conteudo_aula
            
        if updates:
            supabase.table("tb_reposicoes").update(updates).eq("id", id_repo).execute()
            
        return {"message": "Atualizado!"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
        

@router.patch("/reposicao/{id_repo}")
def atualizar_reposicao_status(id_repo: str, dados: ReposicaoUpdate, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)
    
    # Adiciona a verificação aqui também
    if not verificar_permissao_repo(id_repo, ctx):
        raise HTTPException(status_code=403, detail="Apenas o criador ou gerência pode alterar.")

    try:
        supabase.table("tb_reposicoes").update({"presenca": dados.presenca, "observacoes": dados.observacoes}).eq("id", id_repo).execute()
        return {"message": "OK"}
    except: raise HTTPException(status_code=400)

# 6. CRM / LEADS

@router.get("/leads-crm")
def get_leads_crm(filtro_unidade: int | None = None, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)
    
    try:
        # Tenta buscar os leads. 
        # IMPORTANTE: Certifique-se que a tabela 'inscricoes' tem a coluna 'id_unidade' no Supabase.
        # Se não tiver, crie a coluna ou remova os filtros de .eq("id_unidade") abaixo.
        query = supabase.table("inscricoes").select("*").order("created_at", desc=True)
        
        # Filtros de Unidade
        if ctx['nivel'] < 9:
            # Se for Vendedor, tenta filtrar. Se a coluna nao existir, isso pode dar erro.
            # Se der erro, remova essa linha temporariamente até criar a coluna no banco.
            query = query.eq("id_unidade", ctx['id_unidade'])
        elif filtro_unidade:
            query = query.eq("id_unidade", filtro_unidade)

        leads = query.execute().data
        
        # Busca CPFs de alunos para comparar
        alunos = supabase.table("tb_alunos").select("cpf").execute().data
        cpfs = set(''.join(filter(str.isdigit, a['cpf'])) for a in alunos if a.get('cpf'))
        
        res = []
        for l in leads:
            cpf_l = ''.join(filter(str.isdigit, l.get('cpf','') or ''))
            res.append({
                "id": l['id'], 
                "nome": l['nome'], 
                "cpf": l.get('cpf','-'), 
                "whatsapp": l['whatsapp'],
                "workshop": l['workshop'], 
                "data_agendada": l['data_agendada'], 
                "status": l.get('status','Pendente'),
                "vendedor": l.get('vendedor','-'), 
                "ja_e_aluno": (cpf_l in cpfs and cpf_l != ''),
                "id_unidade": l.get('id_unidade') 
            })
        return res
    except Exception as e:
        print(f"Erro CRM: {e}") 
        # Retorna lista vazia em vez de erro 500 para não travar a tela
        return []


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
            
        if auth_up: 
            supabase.auth.admin.update_user_by_id(user_id, auth_up)
            
        return {"message": "Perfil atualizado!"}
    except Exception as e: 
        # Melhoria: Mostra o erro real no log do servidor
        print(f"Erro ao atualizar perfil: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/listar-turmas")
def admin_listar_turmas(authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)
    try:
        query = supabase.table("tb_turmas").select("*")
        if ctx['nivel'] < 9: query = query.eq("id_unidade", ctx['id_unidade'])
        return query.execute().data
    except: return []


@router.get("/listar-professores")
def admin_listar_professores(authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)
    try:
        query = supabase.table("tb_colaboradores").select("id_colaborador, nome_completo").in_("id_cargo", [6, 4])
        if ctx['nivel'] < 9: query = query.eq("id_unidade", ctx['id_unidade'])
        return query.execute().data
    except: return []


@router.get("/chat/conversas-ativas")
def admin_listar_conversas_ativas(authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401, detail="Token ausente")
    try:
        token = authorization.split(" ")[1]
        ctx = get_contexto_usuario(token) # Pega contexto para filtrar por cidade

        lista_alunos_permitidos = []
        filtrar_por_aluno = False

        # Se for Professor (Nível 5) -> Vê só seus alunos (lógica original)
        # Se for Coord/Vendedor (Nível < 9) -> Vê alunos da sua UNIDADE
        
        if ctx['nivel'] == 5: # Professor
            turmas_resp = supabase.table("tb_turmas").select("codigo_turma").eq("id_professor", ctx['id_colaborador']).execute()
            codigos_turmas = [t['codigo_turma'] for t in turmas_resp.data]
            if not codigos_turmas: return []
            matriculas_resp = supabase.table("tb_matriculas").select("id_aluno").in_("codigo_turma", codigos_turmas).execute()
            lista_alunos_permitidos = [m['id_aluno'] for m in matriculas_resp.data]
            filtrar_por_aluno = True
            
        elif ctx['nivel'] < 9: # Coord/Vendedor da unidade
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
                conversas[aid] = {
                    "id_aluno": aid,
                    "nome": m['tb_alunos']['nome_completo'],
                    "ultima_msg": m['mensagem'],
                    "data": m['created_at']
                }
        return list(conversas.values())

    except Exception as e:
        print(f"Erro ao listar conversas: {e}")
        return []


@router.get("/chat/mensagens/{id_aluno}")
def admin_ler_mensagens(id_aluno: int, authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Token ausente")
    try:
        token = authorization.split(" ")[1]
        ctx = get_contexto_usuario(token)

        # Permissões:
        # - Professor (nível 5) só vê alunos das próprias turmas
        # - Coord/Vendedor/Secretaria (nível < 9) só vê alunos da sua unidade
        # - Gerência/Diretoria (>= 9) vê tudo
        permitido = True

        if ctx['nivel'] == 5:
            turmas_resp = supabase.table("tb_turmas").select("codigo_turma").eq("id_professor", ctx['id_colaborador']).execute()
            codigos = [t['codigo_turma'] for t in turmas_resp.data]
            if not codigos:
                permitido = False
            else:
                matr_resp = supabase.table("tb_matriculas").select("id_aluno").in_("codigo_turma", codigos).execute()
                alunos = [m['id_aluno'] for m in matr_resp.data]
                permitido = id_aluno in alunos

        elif ctx['nivel'] < 9:
            alunos_unidade = supabase.table("tb_alunos").select("id_aluno").eq("id_unidade", ctx['id_unidade']).execute()
            alunos = [a['id_aluno'] for a in alunos_unidade.data]
            permitido = id_aluno in alunos

        if not permitido:
            raise HTTPException(status_code=403, detail="Sem permissão para acessar esta conversa")

        msgs = supabase.table("tb_chat").select("*").eq("id_aluno", id_aluno).order("created_at").execute()
        return msgs.data

    except HTTPException:
        raise
    except Exception as e:
        print(f"Erro ao ler mensagens: {e}")
        raise HTTPException(status_code=500, detail="Erro ao buscar mensagens")


@router.post("/chat/responder")
def admin_responder(dados: ChatAdminReply, authorization: str = Header(None)):
    if not authorization: 
        raise HTTPException(status_code=401, detail="Token ausente")
    
    try:
        token = authorization.split(" ")[1]
        ctx = get_contexto_usuario(token) # Agora traz o 'id_cargo' corretamente
        
        id_colab_save = None
        
        # LÓGICA CORRIGIDA:
        # Se for Nível 4 (Coord), 5/6 (Prof) ou >= 8 (Gerente/Diretor),
        # salvamos o ID para a mensagem aparecer no chat privado (com foto e nome).
        # Se for Nível 3 (Vendedor) ou 2 (Secretaria), fica NULL (Suporte Geral).
        
        if ctx['nivel'] >= 4:
            id_colab_save = ctx['id_colaborador']
            
        supabase.table("tb_chat").insert({
            "id_aluno": dados.id_aluno,
            "mensagem": dados.mensagem,
            "enviado_por_admin": True,
            "id_colaborador": id_colab_save
        }).execute()
        
        return {"message": "Respondido"}
    except Exception as e:
        print(f"Erro ao responder: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard-stats")
def get_dashboard_stats(authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)
    
    try:
        # 1. CRM / LEADS
        q_leads = supabase.table("inscricoes").select("status", count="exact")
        if ctx['nivel'] < 9: q_leads = q_leads.eq("id_unidade", ctx['id_unidade'])
        leads_data = q_leads.execute().data
        
        pendentes = sum(1 for l in leads_data if l.get('status') == 'Pendente')
        atendimento = sum(1 for l in leads_data if l.get('status') == 'Em Atendimento')
        matriculados = sum(1 for l in leads_data if l.get('status') == 'Matriculado')
        perdidos = sum(1 for l in leads_data if l.get('status') == 'Perdido')
        total_leads = len(leads_data)
        taxa_conversao = (matriculados / total_leads * 100) if total_leads > 0 else 0

        # 2. ALUNOS E TURMAS
        q_alunos = supabase.table("tb_alunos").select("id_aluno", count="exact")
        if ctx['nivel'] < 9: q_alunos = q_alunos.eq("id_unidade", ctx['id_unidade'])
        total_alunos = q_alunos.execute().count

        q_turmas = supabase.table("tb_turmas").select("status, nome_curso")
        if ctx['nivel'] < 9: q_turmas = q_turmas.eq("id_unidade", ctx['id_unidade'])
        turmas_data = q_turmas.execute().data
        
        # CORREÇÃO: Turmas Ativas agora são "Em Andamento" (Vagas) + "Fechada" (Lotada/Sem Vagas)
        turmas_ativas = sum(1 for t in turmas_data if t['status'] in ['Em Andamento', 'Fechada'])
        
        # Agrupar cursos para o gráfico
        cursos_map = {}
        for t in turmas_data:
            nome = t.get('nome_curso', 'Outros')
            cursos_map[nome] = cursos_map.get(nome, 0) + 1

        # 3. REPOSIÇÕES PENDENTES
        repo_count = supabase.table("tb_reposicoes").select("id", count="exact").eq("status", "Agendada").execute().count

        return {
            "leads": { "pendentes": pendentes, "atendimento": atendimento, "matriculados": matriculados, "perdidos": perdidos, "total": total_leads, "conversao": round(taxa_conversao, 1) },
            "escola": { "total_alunos": total_alunos, "turmas_ativas": turmas_ativas },
            "reposicoes": repo_count,
            "grafico_cursos": cursos_map
        }
    except Exception as e:
        print(f"Erro dashboard: {e}")
        return {}



class MensagemDiretaData(BaseModel):
    id_colaborador: int | None = None # Se for None, é suporte geral
    mensagem: str


@router.get("/chat/historico-unificado")
def get_historico_unificado(authorization: str = Header(None)):
    """
    Retorna uma lista unificada de conversas recentes (Alunos e Grupos),
    ordenada pela mensagem mais recente.
    """
    if not authorization: raise HTTPException(status_code=401)
    try:
        # 1. Buscar últimas mensagens privadas (tb_chat)
        # Limitamos a 200 para pegar histórico recente sem pesar o banco
        chats_privados = supabase.table("tb_chat")\
            .select("*, tb_alunos(nome_completo)")\
            .order("created_at", desc=True)\
            .limit(200)\
            .execute()
            
        # 2. Buscar últimas mensagens de grupo (tb_chat_turma)
        chats_grupos = supabase.table("tb_chat_turma")\
            .select("*")\
            .order("created_at", desc=True)\
            .limit(200)\
            .execute()
            
        historico = []
        ids_processados = set() # Para evitar duplicatas (mostrar só a última do aluno)
        grupos_processados = set() # Para evitar duplicatas (mostrar só a última do grupo)

        # Processar Privados
        for c in chats_privados.data:
            id_aluno = c['id_aluno']
            if id_aluno not in ids_processados:
                nome = "Aluno Desconhecido"
                if c.get('tb_alunos'):
                    nome = c['tb_alunos']['nome_completo']
                
                historico.append({
                    "tipo": "privado",
                    "id": id_aluno,
                    "nome": nome,
                    "ultima_msg": c['mensagem'],
                    "timestamp": c['created_at'],
                    "lida": c['lida']
                })
                ids_processados.add(id_aluno)

        # Processar Grupos
        for g in chats_grupos.data:
            cod_turma = g['codigo_turma']
            if cod_turma not in grupos_processados:
                historico.append({
                    "tipo": "grupo",
                    "id": cod_turma, # O ID do grupo é o código da turma
                    "nome": f"Grupo {cod_turma}",
                    "ultima_msg": f"{g['nome_exibicao']}: {g['mensagem']}",
                    "timestamp": g['created_at'],
                    "lida": True # Grupos não têm status de lido individual
                })
                grupos_processados.add(cod_turma)

        # 3. Ordenar tudo por data (mais recente primeiro)
        historico.sort(key=lambda x: x['timestamp'], reverse=True)
        
        return historico

    except Exception as e:
        print(f"Erro historico unificado: {e}")
        return []



@router.get("/aula/{id_aula}/conteudo")
def get_aula_conteudo(id_aula: int, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)
    
    try:
        # 1. Se for Professor (Nível 5), tenta buscar a VERSÃO DELE primeiro
        if ctx['nivel'] == 5:
            try:
                # .maybe_single() retorna None se não achar, sem dar erro
                personalizado = supabase.table("conteudos_personalizados")\
                    .select("conteudo")\
                    .eq("id_aula", id_aula)\
                    .eq("id_professor", ctx['id_colaborador'])\
                    .maybe_single()\
                    .execute()
                
                # Se achou conteúdo personalizado, retorna ele
                if personalizado.data and personalizado.data.get('conteudo'):
                    return {"html": personalizado.data['conteudo'], "tipo": "personalizado"}
            except Exception as e:
                print(f"Erro ao buscar personalizado (ignorando): {e}")

        # 2. Se não achou personalizado (ou se é Coordenação), busca o CONTEÚDO BASE
        base = supabase.table("aulas").select("conteudo").eq("id", id_aula).maybe_single().execute()
        
        if base.data and base.data.get('conteudo'):
            return {"html": base.data['conteudo'], "tipo": "base"}
        
        # 3. Se não tem em lugar nenhum, retorna vazio para começar do zero
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
        # A. COORDENAÇÃO (Nível 8+): Edita a AULA BASE (Afeta todos que não tem cópia)
        if ctx['nivel'] >= 8:
            supabase.table("aulas").update({"conteudo": dados.conteudo}).eq("id", id_aula).execute()
            return {"message": "Conteúdo BASE atualizado (Modo Coordenação)."}

        # B. PROFESSOR (Nível 5): Salva na tabela PERSONALIZADA (Cópia dele)
        elif ctx['nivel'] == 5:
            payload = {
                "id_aula": id_aula,
                "id_professor": ctx['id_colaborador'],
                "conteudo": dados.conteudo
            }
            # Upsert garante que cria se não existe, ou atualiza se já existe
            # Requer que a tabela tenha constraint unique(id_aula, id_professor)
            supabase.table("conteudos_personalizados").upsert(payload, on_conflict="id_aula,id_professor").execute()
            
            return {"message": "Sua versão personalizada foi salva!"}
        
        else:
            raise HTTPException(status_code=403, detail="Sem permissão para editar.")

    except Exception as e:
        print(f"Erro ao salvar: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao salvar: {str(e)}")
        
@router.get("/conteudo-didatico/cursos")
def admin_listar_cursos_didaticos(authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    try:
        # Esta consulta busca os cursos, seus módulos e as aulas vinculadas
        # Certifique-se que as tabelas 'cursos', 'modulos' e 'aulas' existem com esses nomes
        resp = supabase.table("cursos")\
            .select("*, modulos(*, aulas(*))")\
            .order("ordem")\
            .execute()
        return resp.data
    except Exception as e:
        print(f"Erro ao listar cursos didáticos: {e}")
        return []

def calcular_progresso_automatico(data_inicio_str, total_aulas):
    if not data_inicio_str:
        return 0
    
    # Converte a data de início da turma (ex: "2023-10-01")
    inicio = datetime.strptime(data_inicio_str, "%Y-%m-%d")
    hoje = datetime.now()
    
    # Calcula a diferença de dias
    dias_passados = (hoje - inicio).days
    
    # Calcula quantas aulas foram liberadas (1 a cada 7 dias)
    aulas_liberadas = (dias_passados // 7) + 1
    
    # Garante que não ultrapasse o total de aulas do curso
    aulas_liberadas = min(aulas_liberadas, total_aulas)
    
    # Retorna a porcentagem
    return round((aulas_liberadas / total_aulas) * 100)

@router.get("/aluno/meus-contatos")
def get_contatos_aluno(authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    try:
        token = authorization.split(" ")[1]
        user = supabase.auth.get_user(token)
        user_id = user.user.id

        # 1. Busca Aluno e Unidade
        aluno_resp = supabase.table("tb_alunos").select("id_aluno, id_unidade").eq("user_id", user_id).single().execute()
        if not aluno_resp.data: return []
        
        id_unidade = aluno_resp.data['id_unidade']
        id_aluno = aluno_resp.data['id_aluno']
        contatos = []

        # --- PARTE A: COORDENADOR (ID Cargo 4) ---
        coords = supabase.table("tb_colaboradores").select("id_colaborador, nome_completo").eq("id_unidade", id_unidade).eq("id_cargo", 4).eq("ativo", True).execute()
        for c in coords.data:
            contatos.append({
                "id": c['id_colaborador'],
                "nome": c['nome_completo'],
                "cargo": "Coordenador Pedagógico",
                "tipo": "Coordenacao",
                "codigo_turma_grupo": None
            })

        # --- PARTE B: GRUPO E PROFESSOR ---
        matricula = supabase.table("tb_matriculas").select("codigo_turma").eq("id_aluno", id_aluno).execute()
        if matricula.data:
            cod_turma = matricula.data[0]['codigo_turma']
            turma_info = supabase.table("tb_turmas").select("nome_curso, id_professor, tb_colaboradores(nome_completo)").eq("codigo_turma", cod_turma).single().execute()
            
            if turma_info.data:
                t = turma_info.data
                # Card do Grupo
                contatos.append({
                    "id": f"grupo-{cod_turma}",
                    "nome": f"Grupo {t['nome_curso']}",
                    "cargo": f"Turma {cod_turma}",
                    "tipo": "Grupo",
                    "codigo_turma_grupo": cod_turma
                })
                # Card do Professor
                if t.get('tb_colaboradores'):
                    contatos.append({
                        "id": t['id_professor'],
                        "nome": t['tb_colaboradores']['nome_completo'],
                        "cargo": f"Prof. {t['nome_curso']}",
                        "tipo": "Professor",
                        "codigo_turma_grupo": None
                    })

        # Suporte Geral
        contatos.append({"id": "geral", "nome": "Suporte Javis", "cargo": "Secretaria", "tipo": "Admin", "codigo_turma_grupo": None})
        return contatos
    except Exception as e:
        print(f"Erro contatos: {e}")
        return []

# Rota para buscar o histórico de mensagens com um contato específico
@router.get("/chat/mensagens-com/{target}")
def get_mensagens_chat(target: str, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    try:
        token = authorization.split(" ")[1]
        user_id = supabase.auth.get_user(token).user.id
        aluno = supabase.table("tb_alunos").select("id_aluno").eq("user_id", user_id).single().execute()
        id_aluno = aluno.data['id_aluno']

        query = supabase.table("tb_chat").select("*").eq("id_aluno", id_aluno)
        
        if target == 'geral':
            query = query.is_("id_colaborador", "null")
        else:
            query = query.eq("id_colaborador", int(target))
            
        res = query.order("created_at").execute()
        return res.data
    except: return []

# Rota para o aluno enviar uma mensagem direta
@router.post("/chat/enviar-direto")
def enviar_mensagem_aluno(dados: dict, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    try:
        token = authorization.split(" ")[1]
        user_id = supabase.auth.get_user(token).user.id
        aluno = supabase.table("tb_alunos").select("id_aluno").eq("user_id", user_id).single().execute()
        
        supabase.table("tb_chat").insert({
            "id_aluno": aluno.data['id_aluno'],
            "mensagem": dados['mensagem'],
            "id_colaborador": dados.get('id_colaborador'),
            "enviado_por_admin": False
        }).execute()
        return {"status": "ok"}
    except: raise HTTPException(status_code=400)

@router.get("/chat/mensagens-grupo/{codigo_turma}")
def get_mensagens_grupo(codigo_turma: str, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    try:
        # Busca mensagens onde o codigo_turma coincide
        res = supabase.table("tb_chat").select("*").eq("codigo_turma", codigo_turma).order("created_at").execute()
        return res.data
    except: return []


# --- ROTAS DO GRUPO DA TURMA (RESTAURADAS) ---

@router.get("/chat/turma/{codigo_turma}")
def get_chat_turma(codigo_turma: str, authorization: str = Header(None)):
    """Lê o histórico de mensagens do grupo da turma"""
    if not authorization: raise HTTPException(status_code=401)
    try:
        msgs = supabase.table("tb_chat_turma")\
            .select("*")\
            .eq("codigo_turma", codigo_turma)\
            .order("created_at", desc=False)\
            .limit(100)\
            .execute()
        return msgs.data
    except Exception as e:
        print(f"Erro chat turma: {e}")
        return []

@router.post("/chat/turma/enviar")
def enviar_chat_turma(dados: MensagemGrupoData, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    try:
        token = authorization.split(" ")[1]
        user = supabase.auth.get_user(token)
        user_id = user.user.id
        
        nome_exibicao = "Usuário"
        cargo_exibicao = "Aluno"
        
        # 1. Tenta buscar como Colaborador de forma segura
        colab_resp = supabase.table("tb_colaboradores").select("nome_completo, id_cargo").eq("user_id", user_id).execute()
        
        # Verificação robusta: se o objeto de resposta existe e tem dados
        if colab_resp and hasattr(colab_resp, 'data') and colab_resp.data:
            c = colab_resp.data[0]
            nome_exibicao = c['nome_completo'].split()[0]
            cargo_exibicao = "Professor" if c['id_cargo'] == 6 else "Staff"
        else:
            # 2. Se não é colaborador, busca na tabela de alunos
            aluno_resp = supabase.table("tb_alunos").select("nome_completo").eq("user_id", user_id).execute()
            if aluno_resp and hasattr(aluno_resp, 'data') and aluno_resp.data:
                nome_exibicao = aluno_resp.data[0]['nome_completo'].split()[0]
                cargo_exibicao = "Aluno"

        # 3. Inserção na tabela
        supabase.table("tb_chat_turma").insert({
            "codigo_turma": dados.codigo_turma,
            "mensagem": dados.mensagem,
            "id_usuario_envio": user_id,
            "nome_exibicao": nome_exibicao,
            "cargo_exibicao": cargo_exibicao
        }).execute()
        
        return {"message": "OK"}
    except Exception as e:
        print(f"Erro ao enviar no grupo: {e}")
        raise HTTPException(status_code=500, detail="Erro interno no servidor.")


@router.get("/aula/{aula_id}")
def get_aula_por_id(aula_id: int, authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401)

    try:
        res = supabase.table("aulas").select("*").eq("id", aula_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Aula não encontrada")
        return res.data[0]
    except HTTPException:
        raise
    except Exception as e:
        print(f"Erro ao buscar aula {aula_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/conteudo-didatico/cursos")
def admin_listar_cursos_didaticos(authorization: str = Header(None)):
    """Busca a árvore completa: Cursos -> Módulos -> Aulas"""
    if not authorization: raise HTTPException(status_code=401)
    try:
        # A consulta abaixo traz a hierarquia completa ordenada
        resp = supabase.table("cursos")\
            .select("*, modulos(*, aulas(*))")\
            .order("ordem")\
            .execute()
        
        # Ordenação manual das sub-listas para garantir a sequência pedagógica
        for curso in resp.data:
            curso['modulos'] = sorted(curso.get('modulos', []), key=lambda x: x.get('ordem', 0))
            for modulo in curso['modulos']:
                modulo['aulas'] = sorted(modulo.get('aulas', []), key=lambda x: x.get('ordem', 0))
                
        return resp.data
    except Exception as e:
        print(f"Erro ao carregar estrutura: {e}")
        return []
@router.post("/criar-login-aluno")
def criar_login_aluno(dados: NovoUsuarioData, authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token ausente")

    token = authorization.split(" ", 1)[1]
    ctx = get_contexto_usuario(token)

    # Permissão: Vendedor (3) e Gerência+ (8+)
    if ctx["nivel"] != 3 and ctx["nivel"] < 8:
        raise HTTPException(status_code=403, detail="Acesso restrito.")

    new_user_id = None

    try:
        # Verifica se aluno existe e ainda não tem user_id
        try:
            aluno = supabase.table("tb_alunos")\
                .select("id_aluno, user_id, id_unidade")\
                .eq("id_aluno", dados.id_aluno)\
                .single()\
                .execute()
        except Exception:
            aluno = None
        # Se não for diretoria (9+) e o aluno não for da mesma unidade, bloqueia
        if ctx["nivel"] < 9 and aluno.data.get("id_unidade") != ctx["id_unidade"]:
            raise HTTPException(status_code=403, detail="Sem permissão para aluno de outra unidade.")

        if not aluno or not aluno.data:
            raise HTTPException(status_code=404, detail="Aluno não encontrado.")

        if aluno.data.get("user_id"):
            raise HTTPException(status_code=400, detail="Aluno já possui login.")

        # Cria usuário no Auth
        user_auth = supabase.auth.admin.create_user({
            "email": dados.email,
            "password": dados.senha,
            "email_confirm": True
        })
        new_user_id = user_auth.user.id

        # Atualiza tb_alunos com user_id e email
        up = supabase.table("tb_alunos").update({
            "email": dados.email,      # precisa existir a coluna
            "user_id": new_user_id
        }).eq("id_aluno", dados.id_aluno).execute()

        if not up.data:
            raise Exception("Falha ao atualizar tb_alunos com user_id/email.")

        return {"message": "Login criado com sucesso!", "user_id": new_user_id}

    except HTTPException:
        # repassa erros HTTP corretamente
        raise
    except Exception as e:
        # rollback: remove usuário do Auth se já criou
        if new_user_id:
            try:
                supabase.auth.admin.delete_user(new_user_id)  # :contentReference[oaicite:2]{index=2}
            except Exception:
                pass

        raise HTTPException(status_code=500, detail=f"Erro ao criar login: {str(e)}")

@router.put("/editar-aluno/{id_aluno}")
def admin_editar_aluno(id_aluno: int, dados: AlunoEdicaoData, authorization: str = Header(None)):
    # Valida header Bearer
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token ausente")

    token = authorization.split(" ", 1)[1]
    ctx = get_contexto_usuario(token)

    # Gerência (8+)
    # Permissão: Vendedor (3) e Gerência+ (8+)
    if ctx["nivel"] != 3 and ctx["nivel"] < 8:
        raise HTTPException(status_code=403, detail="Acesso restrito.")

    try:
        # Busca aluno (inclui email pra possível rollback)
        aluno_resp = (
            supabase.table("tb_alunos")
            .select("id_aluno,id_unidade,user_id,email")
            .eq("id_aluno", id_aluno)
            .single()
            .execute()
        )

        if not aluno_resp.data:
            raise HTTPException(status_code=404, detail="Aluno não encontrado.")

        aluno = aluno_resp.data

        # Se não for nível 9+, só edita aluno da própria unidade
        if ctx["nivel"] < 9 and aluno.get("id_unidade") != ctx["id_unidade"]:
            raise HTTPException(status_code=403, detail="Sem permissão para editar aluno de outra unidade.")

        updates = {}

        if getattr(dados, "nome", None):
            updates["nome_completo"] = dados.nome.upper()

        if getattr(dados, "cpf", None):
            updates["cpf"] = dados.cpf

        if getattr(dados, "celular", None):
            updates["celular"] = dados.celular

        if getattr(dados, "telefone", None):
            updates["telefone"] = dados.telefone

        # ✅ Email: versão MAIS SEGURA
        # - atualiza o Auth SEM auto-confirmar
        # - depois atualiza tb_alunos
        # - se tb_alunos falhar, tenta reverter o Auth pro email anterior (se houver)
        novo_email = getattr(dados, "email", None)
        if novo_email:
            novo_email = novo_email.strip().lower()

            user_id = aluno.get("user_id")
            if not user_id:
                raise HTTPException(
                    status_code=400,
                    detail="Aluno não possui login (user_id). Use 'Criar Login do Aluno' antes de alterar o e-mail."
                )

            email_anterior_db = (aluno.get("email") or "").strip().lower() or None

            # 1) Atualiza email do Auth (SEM email_confirm)
            # Isso pode disparar fluxo de confirmação dependendo das configs do Supabase Auth.
            supabase.auth.admin.update_user_by_id(str(user_id), {"email": novo_email})

            # 2) Atualiza tb_alunos
            try:
                updates["email"] = novo_email
                supabase.table("tb_alunos").update(updates).eq("id_aluno", id_aluno).execute()
                # remove do updates pra não re-updar duas vezes abaixo
                updates.pop("email", None)
            except Exception as e_db:
                # 3) rollback do Auth (melhor esforço)
                if email_anterior_db:
                    try:
                        supabase.auth.admin.update_user_by_id(str(user_id), {"email": email_anterior_db})
                    except Exception:
                        pass
                raise HTTPException(status_code=400, detail=f"Erro ao salvar e-mail no aluno: {str(e_db)}")

        # Atualiza tb_alunos (demais campos)
        if updates:
            supabase.table("tb_alunos").update(updates).eq("id_aluno", id_aluno).execute()

        # Atualiza turma na matrícula (se vier turma_codigo)
        turma_codigo = getattr(dados, "turma_codigo", None)
        if turma_codigo:
            # tb_matriculas não tem created_at no seu caso -> usa id_matricula
            mats = (
                supabase.table("tb_matriculas")
                .select("id_matricula")
                .eq("id_aluno", id_aluno)
                .order("id_matricula", desc=True)
                .limit(1)
                .execute()
            )

            if mats.data:
                supabase.table("tb_matriculas").update({"codigo_turma": turma_codigo}).eq(
                    "id_matricula", mats.data[0]["id_matricula"]
                ).execute()
            else:
                supabase.table("tb_matriculas").insert({
                    "id_aluno": id_aluno,
                    "codigo_turma": turma_codigo,
                    "id_vendedor": ctx["id_colaborador"],
                    "status_financeiro": "Ok"
                }).execute()

        return {"message": "Aluno atualizado!"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao atualizar aluno: {str(e)}")


# 7. SISTEMA DE CHAMADA

@router.get("/chamada/turma/{codigo_turma}")
def listar_alunos_chamada(codigo_turma: str, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    try:
        # Busca alunos matriculados na turma específica
        resp = supabase.table("tb_matriculas")\
            .select("id_aluno, tb_alunos(nome_completo)")\
            .eq("codigo_turma", codigo_turma)\
            .execute()
        return resp.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/chamada/salvar")
def salvar_chamada(dados: list, authorization: str = Header(None)):
    if not authorization: raise HTTPException(status_code=401)
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)
    try:
        # Os dados devem vir como uma lista de objetos: [{id_aluno, codigo_turma, presenca}]
        for item in dados:
            item['id_professor'] = ctx['id_colaborador']
            item['data_aula'] = datetime.now().strftime("%Y-%m-%d")
            
        supabase.table("tb_chamadas").upsert(dados, on_conflict="id_aluno,codigo_turma,data_aula").execute()
        return {"message": "Chamada realizada com sucesso!"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    
@router.get("/festas-aniversario")
def listar_festas_aniversario(
    status: Optional[str] = None,
    q: Optional[str] = None,
    data_ini: Optional[str] = None,
    data_fim: Optional[str] = None,
    id_vendedor: Optional[int] = None,
    id_unidade: Optional[int] = None,
    sort_by: str = "data_festa",
    sort_dir: str = "asc",
    authorization: str = Header(None)
):
    if not authorization:
        raise HTTPException(status_code=401)

    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)

    # Nível de acesso (8, 9 ou 10)
    if ctx["nivel"] not in (8, 9, 10):
        raise HTTPException(status_code=403, detail="Acesso restrito.")

    try:
        # CONSULTA FLEXÍVEL: Removidos os "!" para evitar erro de nome de relação
        query = supabase.table("tb_festas_aniversario").select(
            "*, tb_unidades(nome_unidade), tb_colaboradores(nome_completo)"
        )

        if status:
            query = query.eq("status", status)
        if data_ini:
            query = query.gte("data_festa", data_ini)
        if data_fim:
            query = query.lte("data_festa", data_fim)
        if id_vendedor:
            query = query.eq("id_vendedor", id_vendedor)

        # Filtro de Unidade automático para nível 8
        if ctx["nivel"] == 8:
            query = query.eq("id_unidade", ctx["id_unidade"])
        elif id_unidade:
            query = query.eq("id_unidade", id_unidade)

        if q:
            query = query.or_(f"contratante.ilike.%{q}%,aniversariante.ilike.%{q}%,telefone.ilike.%{q}%")

        # Ordenação
        desc = (sort_dir or "").lower() == "desc"
        query = query.order(sort_by, desc=desc)

        return query.execute().data

    except Exception as e:
        print(f"Erro listar festas: {e}")
        # Fallback simples caso o join complexo falhe
        try:
            return supabase.table("tb_festas_aniversario").select("*").execute().data
        except:
            return []
@router.get("/festas-aniversario/vendedores")
def listar_vendedores_festas(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401)

    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)

    if ctx["nivel"] not in (8, 9, 10):
        raise HTTPException(status_code=403, detail="Acesso restrito (nível 8/9/10).")

    try:
        q = supabase.table("tb_colaboradores").select("id_colaborador, nome_completo").eq("ativo", True)

        # nível 8: só da unidade dele
        if ctx["nivel"] == 8:
            q = q.eq("id_unidade", ctx["id_unidade"])

        # nível 9/10: pode ver todos (ou você pode filtrar depois por parâmetro se quiser)
        q = q.order("nome_completo")

        return q.execute().data
    except Exception as e:
        print("Erro vendedores:", e)
        raise HTTPException(status_code=500, detail=str(e))
    
@router.post("/festas-aniversario")
def criar_festa_aniversario(dados: FestaAniversarioCreate, authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401)

    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)

    if ctx["nivel"] not in (8, 9, 10):
        raise HTTPException(status_code=403, detail="Acesso restrito (nível 8/9/10).")

    payload = dados.model_dump(exclude_none=True)

    # unidade:
    # nível 8: força unidade do contexto
    if ctx["nivel"] == 8:
        payload["id_unidade"] = ctx["id_unidade"]
    else:
        # se não veio, default Cuiabá (=1)
        payload["id_unidade"] = payload.get("id_unidade") or 1

    # tipo fixo
    payload["tipo"] = "ANIVERSARIO_GAMER"

    try:
        resp = supabase.table("tb_festas_aniversario").insert(payload).execute()
        return resp.data[0] if resp.data else {"message": "ok"}
    except Exception as e:
        print("Erro criar festa:", e)
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/festas-aniversario/{id_festa}")
def editar_festa_aniversario(id_festa: int, dados: FestaAniversarioUpdate, authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401)

    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)

    if ctx["nivel"] not in (8, 9, 10):
        raise HTTPException(status_code=403, detail="Acesso restrito (nível 8/9/10).")

    updates = dados.model_dump(exclude_none=True)

    try:
        # valida unidade quando nível 8
        if ctx["nivel"] == 8:
            festa = supabase.table("tb_festas_aniversario").select("id_unidade").eq("id", id_festa).single().execute()
            if not festa.data:
                raise HTTPException(status_code=404, detail="Festa não encontrada.")
            if festa.data.get("id_unidade") != ctx["id_unidade"]:
                raise HTTPException(status_code=403, detail="Sem permissão para editar festa de outra unidade.")

            # garante que nível 8 não troca unidade
            updates.pop("id_unidade", None)

        supabase.table("tb_festas_aniversario").update(updates).eq("id", id_festa).execute()
        return {"message": "Festa atualizada!"}

    except HTTPException:
        raise
    except Exception as e:
        print("Erro editar festa:", e)
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/aulas-experimentais/vendedores")
def listar_vendedores_aulas_experimentais(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401)

    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)

    if ctx["nivel"] != 3 and ctx["nivel"] < 8:
        raise HTTPException(status_code=403)

    # Use o 'supabase' global em vez de db
    q = supabase.table("tb_colaboradores").select("id_colaborador,nome_completo").eq("ativo", True)

    if ctx["nivel"] < 9:
        q = q.eq("id_unidade", ctx["id_unidade"])

    return q.order("nome_completo").execute().data or []

def supabase_authed(token: str) -> Client:
    client = create_client(url, key)
    client.postgrest.auth(token)
    return client


@router.get("/relatorio-frequencia-geral")
def listar_frequencia_geral(
    q: Optional[str] = None,
    authorization: str = Header(None)
):
    if not authorization:
        raise HTTPException(status_code=401)
    
    token = authorization.split(" ")[1]
    ctx = get_contexto_usuario(token)

    try:
        # Consulta simples na nova tabela para evitar erro 500
        query = supabase.table("tb_frequencia_eventos").select("*")

        # Se não for nível 9+, filtra pela unidade do colaborador (se a coluna existir)
        # Se sua tabela NÃO tiver id_unidade, remova as duas linhas abaixo
        # if ctx['nivel'] < 9:
        #    query = query.eq("id_unidade", ctx['id_unidade'])

        if q:
            query = query.ilike("nome", f"%{q}%")
            
        resp = query.order("data_aula", desc=True).limit(200).execute()
        return resp.data # Retorna a lista diretamente
    except Exception as e:
        logger.error(f"Erro na tabela frequencia_eventos: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/dashboard-frequencia-stats")
def stats_frequencia(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401)
    
    try:
        # Busca todos os status para contar
        resp = supabase.table("tb_frequencia_eventos").select("status").execute()
        dados = resp.data
        
        presencas = sum(1 for item in dados if item['status'] == 'P')
        faltas = sum(1 for item in dados if item['status'] == 'F')
        
        return {
            "presencas": presencas,
            "faltas": faltas,
            "total": len(dados)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
