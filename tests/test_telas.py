"""O modo telas: parse de formulário e catálogo, efeito de ação, login e upload.

Os parsers são exercitados com HTML na forma que o Ahreas emite (RadMenu, campos
WebForms, RadGrid). O login e o upload usam respx com a mesma coreografia do ERP —
GET pega o estado, POST o devolve — sem tocar num Ahreas real.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from ahreas_mcp.telas import catalogo, executor, formulario
from ahreas_mcp.telas import sessao_web as sw

# --- parsers (puros, sem rede) ----------------------------------------------


def test_formulario_separa_campos_e_acoes():
    html = """
    <form>
      <input type="hidden" name="__VIEWSTATE" value="x"/>
      <input type="text" name="txtCondominio" title="Condomínio"/>
      <select name="cboTipo"><option value="1">Um</option><option value="2">Dois</option></select>
      <input type="checkbox" name="ckSepara"/>
      <input type="submit" name="btnFiltrar" value="Filtrar"/>
      <a href="javascript:__doPostBack('lnkExportar','')">Exportar</a>
    </form>
    """
    form = formulario.analisar(html)
    nomes = {c.nome for c in form.campos}
    assert nomes == {"txtCondominio", "cboTipo", "ckSepara"}  # __VIEWSTATE fica de fora
    tipos = {c.nome: c.tipo for c in form.campos}
    assert tipos["cboTipo"] == "select"
    alvos = {a.alvo for a in form.acoes}
    assert {"btnFiltrar", "lnkExportar"} <= alvos


def test_catalogo_extrai_telas_do_radmenu():
    html = (
        '<span class="rmLink" target="/condominioweb/receber/rateio/Tarifa.aspx">'
        "Tarifas de Consumo</span>"
        '<span class="rmLink" target="/condominioweb/receber/recibos/boletos.aspx">'
        "Boletos Bancários</span>"
    )
    telas = catalogo._extrair(html, "condominioweb")
    caminhos = {t.caminho for t in telas}
    assert "/receber/rateio/Tarifa.aspx" in caminhos
    nomes = {t.nome for t in telas}
    assert "Tarifas de Consumo" in nomes


def test_efeito_da_acao():
    assert executor.efeito_da_acao("btnProcessar") is executor.EfeitoAcao.ESCRITA
    assert executor.efeito_da_acao("btnFiltrar") is executor.EfeitoAcao.LEITURA
    assert executor.efeito_da_acao("btnConsultar") is executor.EfeitoAcao.LEITURA
    # Desconhecido, sem pista -> escrita (pede confirmação).
    assert executor.efeito_da_acao("btnXPTO") is executor.EfeitoAcao.ESCRITA


def test_extrai_tabela_de_radgrid():
    html = """
    <table>
      <tr class="rgRow"><td>Apto 101</td><td>R$ 150,00</td></tr>
      <tr class="rgAltRow"><td>Apto 102</td><td>R$ 220,00</td></tr>
    </table>
    """
    _, linhas, _ = executor.extrair_tabela(html)
    assert linhas == [["Apto 101", "R$ 150,00"], ["Apto 102", "R$ 220,00"]]


def test_extrai_campos_editaveis_de_grid():
    # Grade de edição: cada linha tem inputs preenchíveis com o nome e o valor.
    html = """
    <table>
      <tr class="rgRow"><td>AG-A</td><td>000101</td>
        <td><input type="text" name="ctl04$txtLeitura" value="85,16"/></td></tr>
    </table>
    """
    _, _, campos = executor.extrair_tabela(html)
    assert campos == [{"campo": "ctl04$txtLeitura", "valor": "85,16", "linha": "AG-A 000101"}]


def test_mensagem_ignora_lixo_de_script():
    # O regex de mensagem não pode devolver JavaScript.
    html = '<span id="lblMensagem">function(x){return x}</span>'
    assert executor._extrair_mensagem(html) is None
    html_ok = '<span id="lblMensagem">Importação concluída com sucesso.</span>'
    assert "sucesso" in (executor._extrair_mensagem(html_ok) or "")


# --- login e upload (respx) --------------------------------------------------

_BASE = "https://ahreas.teste.local/condominioweb"


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setenv("AHREAS_BASE_URL", "https://ahreas.teste.local")
    monkeypatch.setenv("AHREAS_CHAVE", "k")
    sw.configuracao.cache_clear()


def _pagina_login() -> str:
    return (
        '<form><input type="hidden" name="__VIEWSTATE" value="v"/>'
        '<input type="hidden" name="__EVENTVALIDATION" value="e"/>'
        '<input name="goLogin$idusuario" id="goLogin_idusuario"/>'
        '<input name="goLogin$idpassword" id="goLogin_idpassword" type="password"/></form>'
    )


def _home_logada() -> str:
    return "<div>Olá, Fulano</div>"  # sem goLogin_idusuario => logado


@respx.mock
async def test_login_web_bem_sucedido():
    respx.get(f"{_BASE}/").mock(return_value=httpx.Response(200, text=_pagina_login()))
    respx.post(f"{_BASE}/").mock(return_value=httpx.Response(200, text=_home_logada()))
    sessao = await sw.entrar("fulano@teste.com", "senha")
    assert isinstance(sessao, sw.SessaoWeb)


@respx.mock
async def test_login_web_recusado():
    respx.get(f"{_BASE}/").mock(return_value=httpx.Response(200, text=_pagina_login()))
    # Resposta ainda com o campo de login => recusado.
    respx.post(f"{_BASE}/").mock(return_value=httpx.Response(200, text=_pagina_login()))
    with pytest.raises(sw.LoginWebRecusado):
        await sw.entrar("fulano@teste.com", "errada")


@respx.mock
async def test_upload_monta_client_state_e_descobre_o_campo():
    # O id do controle (aqui MeuUpload) sai do HTML; o campo é <id>_ClientState.
    html_tela = (
        '<div id="MeuUpload" class="RadAsyncUpload RadUpload"></div>'
        '$create(Telerik.Web.UI.RadAsyncUpload, {"_serializedConfiguration":"CFG",'
        '"_serializedConfigurationType":"TIPO"}, null, null, $get("MeuUpload"));'
    )
    respx.post(f"{_BASE}/Telerik.Web.UI.WebResource.axd").mock(
        return_value=httpx.Response(
            200,
            json={"fileInfo": {"FileName": "a.txt", "ContentLength": 3}, "metaData": "TOKEN123"},
        )
    )
    sessao = sw.SessaoWeb(base_web=_BASE, cookies={})
    estado, campo = await sessao.enviar_arquivo(
        html_tela, "/tela.aspx", "a.txt", b"abc", "text/plain"
    )
    assert "TOKEN123" in estado and "uploadedFiles" in estado
    # O campo foi descoberto do HTML, não presumido.
    assert campo == "MeuUpload_ClientState"


# --- postback AJAX (RadAjax/UpdatePanel) e RadNumericTextBox -----------------


def test_delta_para_html_reconstroi_painel_e_ocultos():
    # Formato do delta: `tamanho|tipo|id|conteudo` repetido. Interessam o painel
    # (HTML novo da grid) e os hiddenField (o __VIEWSTATE novo).
    delta = "12|updatePanel|pnl|<div>G</div>|5|hiddenField|__VIEWSTATE|NEWVS|"
    html = sw._delta_para_html(delta)
    assert "<div>G</div>" in html
    assert 'name="__VIEWSTATE" value="NEWVS"' in html


def test_reescreve_clientstate_preserva_outras_chaves():
    atual = '{"enabled":true,"minValue":0,"validationText":"1","valueAsString":"1"}'
    novo = executor._reescrever_clientstate(atual, "9761")
    import json as _j

    d = _j.loads(novo)
    assert d["validationText"] == "9761"
    assert d["valueAsString"] == "9761"
    assert d["minValue"] == 0  # chave alheia ao valor foi mantida


def test_reescreve_clientstate_sem_json_monta_minimo():
    novo = executor._reescrever_clientstate("", "36")
    import json as _j

    d = _j.loads(novo)
    assert d["validationText"] == "36" and d["valueAsString"] == "36"


_TELA_AJAX = (
    '<form><input type="hidden" name="__VIEWSTATE" value="v"/>'
    '<input type="hidden" name="goHeader1_RadScriptManager1_TSM" value="tsm"/>'
    '<div id="goHeader1_pnlGeralPanel">'
    '<input type="text" name="txtNumeroLeituraFiltro" value=""/>'
    '<input type="hidden" name="txtNumeroLeituraFiltro_ClientState"'
    ' value="{&quot;validationText&quot;:&quot;&quot;,&quot;valueAsString&quot;:&quot;&quot;}"/>'
    '<input type="image" name="imgFiltrar" id="imgFiltrar"/></div></form>'
)


@respx.mock
async def test_postar_ajax_imagem_manda_scriptmanager_e_coordenadas():
    capturado = {}

    def _resp(request):
        capturado["body"] = request.content.decode()
        # Delta com uma grid nova, como o Ahreas devolve num postback parcial.
        delta = "17|updatePanel|goHeader1_pnlGeralPanel|<table>OK</table>|"
        return httpx.Response(200, text=delta)

    respx.post(f"{_BASE}/tela.aspx").mock(side_effect=_resp)
    sessao = sw.SessaoWeb(base_web=_BASE, cookies={})
    sessao._ultimo_html["/tela.aspx"] = _TELA_AJAX
    html = await sessao.postar(
        "/tela.aspx", sw.campos_ocultos(_TELA_AJAX), "imgFiltrar", imagem=True
    )
    corpo = capturado["body"]
    # O ScriptManager identifica painel|controle; a imagem manda .x/.y; o
    # __EVENTTARGET fica vazio (o clique não é por evento).
    assert "goHeader1%24RadScriptManager1=goHeader1%24pnlGeralPanel%7CimgFiltrar" in corpo
    assert "imgFiltrar.x=1" in corpo and "imgFiltrar.y=1" in corpo
    assert "__EVENTTARGET=&" in corpo or corpo.endswith("__EVENTTARGET=")
    # A resposta delta virou página de novo.
    assert "<table>OK</table>" in html


async def test_executar_reescreve_clientstate_do_filtro(monkeypatch):
    # Ao setar um RadNumericTextBox, o executor reescreve o _ClientState espelho
    # sem que quem chama precise fabricar o JSON.
    visto = {}

    async def _postar_fake(caminho, campos, alvo, argumento="", imagem=False):
        visto.update(campos)
        return "<html><title>ok</title></html>"

    sessao = sw.SessaoWeb(base_web=_BASE, cookies={})
    sessao._ultimo_html["/tela.aspx"] = _TELA_AJAX
    monkeypatch.setattr(sessao, "postar", _postar_fake)
    await executor.executar(
        sessao, "/tela.aspx", {"txtNumeroLeituraFiltro": "9761"}, "imgFiltrar", continuar=True
    )
    import json as _j

    espelho = _j.loads(visto["txtNumeroLeituraFiltro_ClientState"])
    assert espelho["validationText"] == "9761" and espelho["valueAsString"] == "9761"
