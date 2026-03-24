"""
Modelos Pydantic para validação de dados
"""
from datetime import date
from pydantic import BaseModel
from typing import Optional

class SprintPedagogica(db.Model):
    __tablename__ = 'sprints_pedagogicas'

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    data_aula = db.Column(db.Date, default=date.today, nullable=False)
    turma_id = db.Column(db.String(50), nullable=False)
    professor_name = db.Column(db.String(100), nullable=False)
    
    # Checklist
    check_chegada_cedo = db.Column(db.Boolean, default=False)
    check_sala_organizada = db.Column(db.Boolean, default=False)
    check_recepcao_alunos = db.Column(db.Boolean, default=False)
    check_foto_grupo_chamada = db.Column(db.Boolean, default=False)
    check_inicio_horario = db.Column(db.Boolean, default=False)
    check_foto_pais = db.Column(db.Boolean, default=False)
    check_chamada_assinada = db.Column(db.Boolean, default=False)
    check_chamada_site = db.Column(db.Boolean, default=False)
    check_ligacao_faltantes = db.Column(db.Boolean, default=False)
    
    observacoes = db.Column(db.Text)

    # Garante que não teremos duas linhas para a mesma turma no mesmo dia
    __table_args__ = (
        db.UniqueConstraint('data_aula', 'turma_id', name='uq_sprint_data_turma'),
    )

class FestaAniversarioCreate(BaseModel):
    status: str = "PARA_ACONTECER"
    data_festa: Optional[str] = None     # YYYY-MM-DD
    horario: Optional[str] = None
    contratante: Optional[str] = None
    telefone: Optional[str] = None
    aniversariante: Optional[str] = None
    idade: Optional[int] = None
    data_pagamento: Optional[str] = None # YYYY-MM-DD
    kit_festa: Optional[bool] = None
    valor: Optional[float] = None
    observacoes: Optional[str] = None

    id_vendedor: Optional[int] = None
    id_unidade: Optional[int] = None


class FestaAniversarioUpdate(BaseModel):
    status: Optional[str] = None
    data_festa: Optional[str] = None
    horario: Optional[str] = None
    contratante: Optional[str] = None
    telefone: Optional[str] = None
    aniversariante: Optional[str] = None
    idade: Optional[int] = None
    data_pagamento: Optional[str] = None
    kit_festa: Optional[bool] = None
    valor: Optional[float] = None
    observacoes: Optional[str] = None

    id_vendedor: Optional[int] = None
    id_unidade: Optional[int] = None

class NovoUsuarioData(BaseModel):
    id_aluno: int
    email: str
    senha: str


class MensagemDiretaData(BaseModel):
    id_colaborador: int | None = None # Se for None, é suporte geral
    mensagem: str

class MensagemGrupoData(BaseModel):
    codigo_turma: str
    mensagem: str

class FuncionarioEdicaoData(BaseModel):
    nome: str | None = None
    telefone: str | None = None
    id_cargo: int | None = None
    ativo: bool | None = None


class PerfilUpdateData(BaseModel):
    nome: str | None = None
    telefone: str | None = None
    email_contato: str | None = None
    email_login: str | None = None
    nova_senha: str | None = None


class ReposicaoEdicaoData(BaseModel):
    data_hora: str | None = None
    conteudo_aula: str | None = None


class ReposicaoUpdate(BaseModel):
    presenca: bool | None = None  # True=Veio, False=Faltou, None=Pendente
    observacoes: str | None = None


class TurmaData(BaseModel):
    codigo: str
    id_professor: int | None = None
    curso: str
    dia_semana: str
    horario: str
    sala: str
    status: str
    tipo: str = "PARTICULAR"  # NOVO CAMPO: Padrão é Particular
    data_inicio: str | None = None
    qtd_aulas: int | None = 0
    data_termino_real: str | None = None


class LoginData(BaseModel):
    email: str
    password: str


class EmailData(BaseModel):
    email: str


class NovoAlunoData(BaseModel):
    nome: str
    email: str
    cpf: str
    senha: str
    turma_codigo: str
    celular: str
    telefone: str | None = None
    data_nascimento: str | None = None


class AlunoEdicaoData(BaseModel):
    nome: str | None = None
    email: str | None = None
    celular: str | None = None
    telefone: str | None = None
    cpf: str | None = None
    turma_codigo: str | None = None  # Para trocar de turma se precisar


class ReposicaoData(BaseModel):
    id_aluno: int
    data_hora: str
    turma_codigo: str
    id_professor: int
    conteudo_aula: str
    motivo: str | None = None
    observacoes: str | None = None


class InscricaoAulaData(BaseModel):
    nome: str
    email: str
    telefone: str
    nascimento: str
    cidade: str
    aceitou_termos: bool


class MensagemChat(BaseModel):
    telefone: str
    texto: str


class ChatMensagemData(BaseModel):
    mensagem: str


class ChatAdminReply(BaseModel):
    id_aluno: int
    mensagem: str


class StatusUpdateData(BaseModel):
    status: str


class NovoFuncionarioData(BaseModel):
    nome: str
    email: str
    senha: str
    telefone: str
    id_cargo: int


class AulaConteudoData(BaseModel):
    conteudo: str

class ItemChamada(BaseModel):
    id_aluno: int
    codigo_turma: str
    data_aula: str
    presenca: bool

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
