import asyncio
import io
import os
import re
import time
from urllib.parse import urlsplit

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from db import (
    adicionar_chaves,
    contar_chaves_disponiveis,
    gerar_chaves,
    load_db,
    marcar_chave_entregue,
    pegar_chave_disponivel,
    save_db,
)
from pix import gerar_payload_pix, gerar_qrcode_bytes
from state import is_dono, rascunhos_painel

load_dotenv()

GUILD_ID = os.environ.get("GUILD_ID")
GUILD_OBJECT = discord.Object(id=int(GUILD_ID)) if GUILD_ID else None

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
purchase_lock = asyncio.Lock()
owner_role_cache = {}


# ---------------- utilitários ----------------

def preco_str(v) -> str:
    return "R$ " + f"{float(v):.2f}".replace(".", ",")


def embed_painel(titulo, descricao, preco, banner) -> discord.Embed:
    embed = discord.Embed(
        title=f" {titulo}",
        description=f"{descricao}\n\n## {preco_str(preco)}",
        color=0x168BFF,
    )
    embed.set_footer(text="FROST SENSI • Compra segura pelo Discord")
    if banner:
        embed.set_image(url=banner)
    return embed


async def acesso_negado(interaction: discord.Interaction):
    await interaction.response.send_message(
        " Você não tem o cargo de dono para usar isso.", ephemeral=True
    )


async def run_db(func, *args, **kwargs):
    """Roda chamadas (síncronas) ao Supabase numa thread separada,
    pra não travar o loop de eventos do bot."""
    return await asyncio.to_thread(func, *args, **kwargs)


def is_dono_cached(member) -> bool:
    if getattr(getattr(member, "guild_permissions", None), "administrator", False):
        return True
    guild = getattr(member, "guild", None)
    if guild and getattr(guild, "owner_id", None) == member.id:
        return True
    role_id = owner_role_cache.get(getattr(guild, "id", None))
    return bool(role_id and any(role.id == int(role_id) for role in getattr(member, "roles", [])))


# ---------------- comandos de barra ----------------

@bot.tree.command(
    name="configurar-cargo-dono",
    description="Define qual cargo pode usar o painel do dono e cadastrar chaves/Pix.",
)
@app_commands.describe(cargo="Cargo que terá acesso de dono no bot")
@app_commands.default_permissions(administrator=True)
async def configurar_cargo_dono(interaction: discord.Interaction, cargo: discord.Role):
    await interaction.response.defer(ephemeral=True, thinking=True)
    db = await run_db(load_db)
    db["discordSettings"]["cargoDonoId"] = str(cargo.id)
    await run_db(save_db, db)
    if interaction.guild:
        owner_role_cache[interaction.guild.id] = cargo.id
    await interaction.followup.send(f"Cargo de dono definido como {cargo.mention}.", ephemeral=True)


@bot.tree.command(
    name="cadastrar-chaves",
    description="Abre um formulário para colar várias chaves separadas por vírgula.",
)
async def cadastrar_chaves(interaction: discord.Interaction):
    if not is_dono_cached(interaction.user):
        return await acesso_negado(interaction)
    await interaction.response.send_modal(ModalCadastrarChaves())


@bot.tree.command(
    name="gerar-chaves",
    description="Gera chaves novas automaticamente e já cadastra no banco.",
)
@app_commands.describe(quantidade="Quantas chaves gerar")
async def gerar_chaves_cmd(
    interaction: discord.Interaction, quantidade: app_commands.Range[int, 1, 500]
):
    await interaction.response.defer(ephemeral=True)
    db = await run_db(load_db)
    if not is_dono(interaction.user, db):
        return await interaction.followup.send("Você não tem permissão para usar isso.", ephemeral=True)

    geradas = gerar_chaves(db, quantidade)
    await run_db(save_db, db)

    arquivo = discord.File(
        io.BytesIO("\n".join(geradas).encode("utf-8")),
        filename=f"chaves-frost-sensi-{int(time.time() * 1000)}.txt",
    )
    await interaction.followup.send(
        content=f" {len(geradas)} chaves geradas e cadastradas no banco.",
        file=arquivo,
    )


@bot.tree.command(
    name="painel-dono",
    description="Abre o painel de administração do bot (só para o cargo de dono).",
)
async def painel_dono(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    db = await run_db(load_db)
    if not is_dono(interaction.user, db):
        return await interaction.followup.send("Você não tem permissão para usar isso.", ephemeral=True)
    if interaction.guild:
        role_id = db["discordSettings"].get("cargoDonoId")
        if role_id:
            owner_role_cache[interaction.guild.id] = int(role_id)

    disponiveis = contar_chaves_disponiveis(db)

    view = discord.ui.View(timeout=300)
    view.add_item(
        discord.ui.Button(
            custom_id="pd_criar_painel", label="Criar painel", style=discord.ButtonStyle.primary
        )
    )
    view.add_item(discord.ui.Button(custom_id="pd_categorias", label="Categorias", style=discord.ButtonStyle.secondary, row=1))
    view.add_item(discord.ui.Button(custom_id="pd_conteudo", label="Conteúdo do produto", style=discord.ButtonStyle.secondary, row=1))
    view.add_item(discord.ui.Button(custom_id="pd_feedback", label="Mensagem de feedback", style=discord.ButtonStyle.secondary, row=1))
    view.add_item(discord.ui.Button(custom_id="pd_canal_vendas", label="Canal de vendas", style=discord.ButtonStyle.secondary, row=2))
    view.add_item(
        discord.ui.Button(
            custom_id="pd_cadastrar_pix", label="Chave Pix", style=discord.ButtonStyle.secondary
        )
    )
    view.add_item(
        discord.ui.Button(
            custom_id="pd_ver_pix",
            label="Ver Pix cadastrados",
            style=discord.ButtonStyle.secondary,
        )
    )

    await interaction.followup.send(
        content=f"**Painel do dono**\nChaves disponíveis para venda: **{disponiveis}**",
        view=view,
        ephemeral=True,
    )


@bot.tree.command(name="desbloquear", description="Cancela um pedido pendente e libera a pessoa para comprar novamente.")
@app_commands.describe(pessoa="Pessoa que deve poder iniciar uma nova compra")
async def desbloquear(interaction: discord.Interaction, pessoa: discord.Member):
    await interaction.response.defer(ephemeral=True, thinking=True)
    db = await run_db(load_db)
    if not is_dono(interaction.user, db):
        return await interaction.followup.send("Você não tem permissão para desbloquear compradores.", ephemeral=True)
    settings = db["discordSettings"]
    matches = [(cid, sale) for cid, sale in settings["vendasPendentes"].items()
               if sale.get("buyerId") == str(pessoa.id)
               and sale.get("status", "aguardando_pagamento") != "entregue"]
    paid_orders = [(cid, sale) for cid, sale in matches if sale.get("status") in ("pago", "entregando")
                   and interaction.guild and interaction.guild.get_channel(int(cid))]
    if paid_orders:
        channels = ", ".join(interaction.guild.get_channel(int(cid)).mention for cid, _ in paid_orders)
        return await interaction.followup.send(f"{pessoa.mention} tem pedido pago em aberto ({channels}). Finalize a entrega antes de liberar outra compra.", ephemeral=True)
    if not matches:
        return await interaction.followup.send(f"{pessoa.mention} já pode comprar ou não tem pedido pendente.", ephemeral=True)
    close_channels = []
    for channel_id, sale in matches:
        key = db["keys"].get(sale.get("keyCode"))
        if key:
            key.pop("reservadaDiscord", None)
        if interaction.guild:
            channel = interaction.guild.get_channel(int(channel_id))
            if isinstance(channel, discord.TextChannel):
                close_channels.append(channel)
        settings["vendasPendentes"].pop(channel_id, None)
        for logged_sale in settings["vendas"]:
            if logged_sale.get("canalId") == channel_id and logged_sale.get("status") != "entregue":
                logged_sale["status"] = "cancelado pelo dono"
    await run_db(save_db, db)
    closed = 0
    for channel in close_channels:
        try:
            await channel.delete(reason=f"Pedido cancelado e {pessoa} desbloqueado pelo dono")
            closed += 1
        except discord.HTTPException as error:
            print(f"Falha ao excluir o canal antigo {channel.id}: {error}")
    await interaction.followup.send(f"{pessoa.mention} foi liberado para comprar novamente. Pedidos pendentes cancelados: {len(matches)}. Canais antigos removidos: {closed}.", ephemeral=True)


# ---------------- modais ----------------

class ModalCadastrarChaves(discord.ui.Modal, title="Cadastrar chaves"):
    chaves_lista = discord.ui.TextInput(
        label="Cole as chaves separadas por vírgula",
        style=discord.TextStyle.paragraph,
        placeholder="CHAVE1, CHAVE2, CHAVE3, ...",
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        db = await run_db(load_db)
        if not is_dono(interaction.user, db):
            return await interaction.followup.send("Você não tem permissão para cadastrar chaves.", ephemeral=True)
        codigos = str(self.chaves_lista.value).split(",")
        adicionadas, repetidas = adicionar_chaves(db, codigos)
        await run_db(save_db, db)
        extra = f" ({repetidas} já existiam e foram ignoradas.)" if repetidas else ""
        await interaction.followup.send(
            f" {adicionadas} chave(s) cadastrada(s).{extra}", ephemeral=True
        )


class ModalCriarPainel(discord.ui.Modal, title="Criar painel de vendas"):
    painel_titulo = discord.ui.TextInput(
        label="Título", style=discord.TextStyle.short, required=True
    )
    painel_desc = discord.ui.TextInput(
        label="Descrição", style=discord.TextStyle.paragraph, required=True
    )
    painel_preco = discord.ui.TextInput(
        label="Preço (ex: 3.00)", style=discord.TextStyle.short, required=True
    )
    painel_banner = discord.ui.TextInput(
        label="URL do banner (640x260, .png)", style=discord.TextStyle.short, required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        titulo = str(self.painel_titulo.value)
        descricao = str(self.painel_desc.value)
        banner = str(self.painel_banner.value).strip()
        parsed_banner = urlsplit(banner)
        if parsed_banner.scheme not in ("http", "https") or not parsed_banner.netloc or any(c.isspace() for c in banner):
            return await interaction.response.send_message("O link do banner precisa ser uma URL http ou https válida.", ephemeral=True)
        try:
            preco = float(str(self.painel_preco.value).replace(",", "."))
        except ValueError:
            preco = None

        if preco is None or preco <= 0:
            return await interaction.response.send_message(
                " Preço inválido. Use algo como 3.00", ephemeral=True
            )

        rascunhos_painel[interaction.user.id] = {
            "titulo": titulo,
            "descricao": descricao,
            "preco": preco,
            "banner": banner,
        }

        view = discord.ui.View(timeout=None)
        view.add_item(
            discord.ui.Button(
                custom_id="pd_enviar_painel", label="Enviar painel", style=discord.ButtonStyle.success
            )
        )

        await interaction.response.send_message(
            content="Pré-visualização do painel:",
            embed=embed_painel(titulo, descricao, preco, banner),
            view=view,
            ephemeral=True,
        )


class ModalCadastrarPix(discord.ui.Modal, title="Cadastrar chave Pix"):
    pix_chave = discord.ui.TextInput(label="Chave Pix", style=discord.TextStyle.short, required=True)
    pix_nome = discord.ui.TextInput(
        label="Nome do recebedor", style=discord.TextStyle.short, required=True
    )
    pix_cidade = discord.ui.TextInput(
        label="Cidade do recebedor", style=discord.TextStyle.short, required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        db = await run_db(load_db)
        if not is_dono(interaction.user, db):
            return await interaction.followup.send("Você não tem permissão para cadastrar uma chave Pix.", ephemeral=True)
        chave = str(self.pix_chave.value).strip()
        nome = str(self.pix_nome.value).strip()
        cidade = str(self.pix_cidade.value).strip()
        pix_id = "pix" + str(int(time.time() * 1000))
        db["discordSettings"]["pixKeys"].append(
            {"id": pix_id, "chave": chave, "nome": nome, "cidade": cidade}
        )
        db["discordSettings"]["pixAtivaId"] = pix_id
        await run_db(save_db, db)
        await interaction.followup.send(
            f"Chave Pix de **{nome}** cadastrada e definida como ativa.", ephemeral=True
        )


class ModalConteudoProduto(discord.ui.Modal, title="Configurar conteúdo do produto"):
    descricao = discord.ui.TextInput(label="Descrição do conteúdo", style=discord.TextStyle.paragraph, max_length=1500, required=True)
    imagem1 = discord.ui.TextInput(label="Imagem 1 (URL, opcional)", required=False, max_length=500)
    imagem2 = discord.ui.TextInput(label="Imagem 2 (URL, opcional)", required=False, max_length=500)
    imagem3 = discord.ui.TextInput(label="Imagem 3 (URL, opcional)", required=False, max_length=500)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        db = await run_db(load_db)
        if not is_dono(interaction.user, db):
            return await interaction.followup.send("Você não tem permissão para configurar o conteúdo.", ephemeral=True)
        urls = [str(x.value).strip() for x in (self.imagem1, self.imagem2, self.imagem3) if str(x.value).strip()]
        if any((urlsplit(u).scheme not in ("https", "http") or not urlsplit(u).netloc or any(c.isspace() for c in u)) for u in urls):
            return await interaction.followup.send("Use links de imagem começando com http:// ou https://.", ephemeral=True)
        db["discordSettings"]["conteudoProduto"] = {"descricao": str(self.descricao.value), "imagens": urls}
        await run_db(save_db, db)
        await interaction.followup.send("Conteúdo do produto salvo para os próximos painéis.", ephemeral=True)


class ModalFeedback(discord.ui.Modal, title="Mensagem após a entrega"):
    mensagem = discord.ui.TextInput(label="Mensagem de agradecimento e feedback", style=discord.TextStyle.paragraph, max_length=1500, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        db = await run_db(load_db)
        if not is_dono(interaction.user, db):
            return await interaction.followup.send("Você não tem permissão para alterar a mensagem.", ephemeral=True)
        db["discordSettings"]["mensagemFeedback"] = str(self.mensagem.value)
        await run_db(save_db, db)
        await interaction.followup.send("Mensagem pós-entrega salva.", ephemeral=True)


# ---------------- botões do painel do dono ----------------

async def on_botao_ver_pix(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    db = await run_db(load_db)
    if not is_dono(interaction.user, db):
        return await interaction.followup.send("Você não tem permissão para gerenciar as chaves Pix.", ephemeral=True)
    lista = db["discordSettings"]["pixKeys"]
    if not lista:
        return await interaction.followup.send(
            "Nenhuma chave Pix cadastrada ainda.", ephemeral=True
        )
    ativa_id = db["discordSettings"]["pixAtivaId"]
    options = [discord.SelectOption(label=f"{p['nome']} — {p['cidade']}"[:100], value=p["id"], description=("Ativa • " if p["id"] == ativa_id else "") + p["chave"][:90]) for p in lista[:25]]
    view = discord.ui.View(timeout=180)
    view.add_item(discord.ui.Select(custom_id="pix_manage_select", placeholder="Selecione uma chave Pix", options=options))
    view.add_item(discord.ui.Button(custom_id="pix_activate", label="Definir como ativa", style=discord.ButtonStyle.primary, row=1))
    view.add_item(discord.ui.Button(custom_id="pix_delete", label="Excluir selecionada", style=discord.ButtonStyle.danger, row=1))
    await interaction.followup.send("Gerencie as chaves Pix cadastradas. A chave ativa é usada nas próximas cobranças.", view=view, ephemeral=True)


# ---------------- enviar painel para um canal ----------------

async def on_botao_enviar_painel(interaction: discord.Interaction):
    rascunho = rascunhos_painel.get(interaction.user.id)
    if not rascunho:
        return await interaction.response.send_message(
            " Esse rascunho expirou, crie o painel de novo.", ephemeral=True
        )
    select = discord.ui.ChannelSelect(
        custom_id="select_canal_painel",
        placeholder="Escolha o canal para enviar o painel",
        channel_types=[discord.ChannelType.text],
    )
    view = discord.ui.View(timeout=None)
    view.add_item(select)
    await interaction.response.send_message(
        content="Em qual canal você deseja enviar esse painel?",
        view=view,
        ephemeral=True,
    )


# ---------------- fluxo de compra ----------------

async def on_botao_comprar(interaction: discord.Interaction, painel_id: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    db = await run_db(load_db)
    painel = db["discordSettings"]["paineis"].get(painel_id)
    if not painel:
        return await interaction.followup.send(
            " Esse painel não existe mais.", ephemeral=True
        )

    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            custom_id=f"buyyes_{painel_id}", label="Sim", style=discord.ButtonStyle.success
        )
    )
    view.add_item(
        discord.ui.Button(
            custom_id=f"buyno_{painel_id}", label="Não", style=discord.ButtonStyle.danger
        )
    )

    await interaction.followup.send(
        content=f"Você deseja comprar **{painel['titulo']}** por **{preco_str(painel['preco'])}**?",
        view=view,
        ephemeral=True,
    )


async def on_botao_ver_produto(interaction: discord.Interaction, painel_id: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    db = await run_db(load_db)
    painel = db["discordSettings"]["paineis"].get(painel_id)
    if not painel:
        return await interaction.followup.send("Este produto não está mais disponível.", ephemeral=True)
    conteudo = painel.get("conteudo") or db["discordSettings"].get("conteudoProduto", {})
    embeds = [discord.Embed(title=f"Conteúdo de {painel['titulo']}", description=conteudo.get("descricao") or "Confira os detalhes deste produto.", color=0x168BFF)]
    for url in conteudo.get("imagens", [])[:3]:
        image_embed = discord.Embed(color=0x168BFF)
        image_embed.set_image(url=url)
        embeds.append(image_embed)
    await interaction.followup.send(embeds=embeds, ephemeral=True)


async def on_botao_comprar_nao(interaction: discord.Interaction):
    await interaction.response.edit_message(content="Compra cancelada.", view=None)


async def on_botao_comprar_sim(interaction: discord.Interaction, painel_id: str):
    await interaction.response.edit_message(content="Preparando seu pedido...", view=None)
    async with purchase_lock:
        db = await run_db(load_db)
        settings = db["discordSettings"]
        open_order = next((cid for cid, v in settings["vendasPendentes"].items()
                           if v.get("buyerId") == str(interaction.user.id)
                           and v.get("status", "aguardando_pagamento") in ("aguardando_pagamento", "pago", "entregando")), None)
        if open_order:
            existing = interaction.guild.get_channel(int(open_order)) if interaction.guild else None
            previous = settings["vendasPendentes"].get(open_order, {})
            if existing:
                msg = f"Você já tem um pedido aberto: {existing.mention}."
                return await interaction.followup.send(msg, ephemeral=True)
            if previous.get("status") in ("pago", "entregando"):
                return await interaction.followup.send("Há um pedido marcado como pago, mas o canal não foi encontrado. Peça ao dono para verificar e, se necessário, usar /desbloquear.", ephemeral=True)
            previous_key = db["keys"].get(previous.get("keyCode"))
            if previous_key and previous.get("status", "aguardando_pagamento") == "aguardando_pagamento":
                previous_key.pop("reservadaDiscord", None)
            settings["vendasPendentes"].pop(open_order, None)
            for logged_sale in settings["vendas"]:
                if logged_sale.get("canalId") == open_order and logged_sale.get("status") != "entregue":
                    logged_sale["status"] = "cancelado: canal do pedido não existe"
            await run_db(save_db, db)
        painel = settings["paineis"].get(painel_id)
        if not painel:
            return await interaction.followup.send("Este painel não está mais disponível.", ephemeral=True)
        category_id = settings.get("categoriaPagamentoId")
        category = interaction.guild.get_channel(int(category_id)) if interaction.guild and category_id else None
        if not isinstance(category, discord.CategoryChannel):
            return await interaction.followup.send("A categoria de pagamentos ainda não foi configurada. Avise o dono do servidor.", ephemeral=True)
        paid_id = settings.get("categoriaPagoId")
        paid_category = interaction.guild.get_channel(int(paid_id)) if paid_id else None
        if not isinstance(paid_category, discord.CategoryChannel):
            return await interaction.followup.send("A categoria de pedidos pagos ainda não foi configurada. Avise o dono do servidor.", ephemeral=True)
        if not settings.get("pixAtivaId"):
            return await interaction.followup.send("Nenhuma chave Pix está ativa no momento. Avise o dono.", ephemeral=True)
        pix = next((p for p in settings["pixKeys"] if p["id"] == settings["pixAtivaId"]), None)
        if not pix:
            return await interaction.followup.send("A chave Pix ativa não foi encontrada. Avise o dono.", ephemeral=True)
        chave = pegar_chave_disponivel(db)
        if not chave:
            return await interaction.followup.send("O estoque de keys está vazio. Avise o dono.", ephemeral=True)
        guild = interaction.guild
        if guild is None:
            return await interaction.followup.send("Inicie a compra dentro do servidor.", ephemeral=True)
        me = guild.me
        if not me or not guild.get_channel(category.id):
            return await interaction.followup.send("Não consegui acessar a categoria configurada. Confira se ela existe e se o bot tem permissão para ver e gerenciar canais.", ephemeral=True)
        bot_perms = category.permissions_for(me)
        if not bot_perms.manage_channels or not bot_perms.view_channel:
            return await interaction.followup.send("Não consigo criar o canal na categoria escolhida. Dê ao bot as permissões de ver canais e gerenciar canais nessa categoria.", ephemeral=True)
        numero = settings.get("contadorCanal", 300)
        cargo_dono_id = settings.get("cargoDonoId")
        cargo_dono = guild.get_role(int(cargo_dono_id)) if cargo_dono_id else None
        owner_member = guild.owner
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True),
        }
        if cargo_dono:
            overwrites[cargo_dono] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        if owner_member:
            overwrites[owner_member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        channel = None
        try:
            channel = await guild.create_text_channel(name=f"pagamento-{numero}", category=category, overwrites=overwrites, reason=f"Pedido de compra de {interaction.user}")
            settings["contadorCanal"] = numero + 1
            chave["reservadaDiscord"] = {"buyerId": str(interaction.user.id), "channelId": str(channel.id)}
            settings["vendasPendentes"][str(channel.id)] = {
                "buyerId": str(interaction.user.id), "painelId": painel_id, "produto": painel["titulo"],
                "preco": painel["preco"], "criadoEm": int(time.time() * 1000),
                "status": "aguardando_pagamento", "keyCode": chave["code"],
            }
            await run_db(save_db, db)
            payload = gerar_payload_pix(chave=pix["chave"], nome=pix["nome"], cidade=pix["cidade"], valor=painel["preco"], txid=str(channel.id)[-20:])
            attachment = discord.File(io.BytesIO(gerar_qrcode_bytes(payload)), filename="pix.png")
            embed = discord.Embed(title=f"Pagamento — {painel['titulo']}", description=f"Status: **Aguardando pagamento**\n\nValor: **{preco_str(painel['preco'])}**\n\nEscaneie o QR Code com o app do seu banco ou copie o código Pix.", color=0x168BFF)
            embed.set_image(url="attachment://pix.png")
            view = discord.ui.View(timeout=None)
            view.add_item(discord.ui.Button(custom_id=f"copiarpix_{channel.id}", label="Copiar Pix", style=discord.ButtonStyle.secondary))
            view.add_item(discord.ui.Button(custom_id=f"confirmar_{channel.id}", label="Confirmar pagamento", style=discord.ButtonStyle.success))
            view.add_item(discord.ui.Button(custom_id=f"fechar_{channel.id}", label="Fechar pedido", style=discord.ButtonStyle.danger))
            role_ping = cargo_dono.mention if cargo_dono else (owner_member.mention if owner_member else "")
            payment_message = await channel.send(content=f"{interaction.user.mention} {role_ping}\nSeu pedido foi aberto. A equipe avisada aqui poderá confirmar o pagamento.", embed=embed, file=attachment, view=view, allowed_mentions=discord.AllowedMentions(users=True, roles=True, everyone=False))
            settings["vendasPendentes"][str(channel.id)]["paymentMessageId"] = str(payment_message.id)
            await run_db(save_db, db)
        except discord.Forbidden:
            if channel:
                try: await channel.delete(reason="Falha de permissão ao iniciar pagamento")
                except discord.HTTPException: pass
            chave.pop("reservadaDiscord", None)
            settings["vendasPendentes"].pop(str(channel.id) if channel else "", None)
            await run_db(save_db, db)
            return await interaction.followup.send("O Discord negou uma permissão ao criar o pedido. Confira as permissões do bot na categoria de pagamentos e tente novamente.", ephemeral=True)
        except Exception:
            if channel:
                try: await channel.delete(reason="Falha ao montar pedido")
                except discord.HTTPException: pass
            chave.pop("reservadaDiscord", None)
            settings["vendasPendentes"].pop(str(channel.id) if channel else "", None)
            try: await run_db(save_db, db)
            except Exception: pass
            raise
        await interaction.followup.send(f"Seu canal privado de pagamento está pronto: {channel.mention}", ephemeral=True)


# ---------------- copiar pix / confirmar compra ----------------

async def on_botao_copiar_pix(interaction: discord.Interaction, canal_id: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    db = await run_db(load_db)
    venda = db["discordSettings"]["vendasPendentes"].get(canal_id)
    if not venda:
        return await interaction.followup.send(
            " Essa cobrança não existe mais.", ephemeral=True
        )
    pix = next(
        p for p in db["discordSettings"]["pixKeys"] if p["id"] == db["discordSettings"]["pixAtivaId"]
    )
    payload = gerar_payload_pix(
        chave=pix["chave"],
        nome=pix["nome"],
        cidade=pix["cidade"],
        valor=venda["preco"],
        txid=canal_id[-20:],
    )
    await interaction.followup.send(f"```{payload}```", ephemeral=True)


async def on_botao_confirmar_compra(interaction: discord.Interaction, canal_id: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    db = await run_db(load_db)
    if not is_dono(interaction.user, db):
        return await interaction.followup.send("Você não tem permissão para confirmar pagamentos.", ephemeral=True)
    settings = db["discordSettings"]
    venda = settings["vendasPendentes"].get(canal_id)
    if not venda:
        return await interaction.followup.send("Este pedido não está mais aberto.", ephemeral=True)
    channel = interaction.guild.get_channel(int(canal_id)) if interaction.guild else interaction.channel
    if venda.get("status") == "pago":
        delivery_message_id = venda.get("deliveryMessageId")
        if isinstance(channel, discord.TextChannel) and delivery_message_id:
            try:
                await channel.fetch_message(int(delivery_message_id))
                return await interaction.followup.send("O pagamento já foi confirmado. O botão Enviar KEY já está no canal.", ephemeral=True)
            except discord.NotFound:
                pass
            except discord.HTTPException:
                return await interaction.followup.send("O pagamento já foi confirmado. Não consegui consultar a mensagem de entrega; verifique o canal pago.", ephemeral=True)
        if isinstance(channel, discord.TextChannel):
            recovery_view = discord.ui.View(timeout=None)
            recovery_view.add_item(discord.ui.Button(custom_id=f"enviarkey_{canal_id}", label="Enviar KEY", style=discord.ButtonStyle.success))
            recovery_view.add_item(discord.ui.Button(custom_id=f"fechar_{canal_id}", label="Fechar pedido", style=discord.ButtonStyle.danger))
            delivery_message = await channel.send("Pagamento confirmado. Use Enviar KEY para entregar o produto ao cliente.", view=recovery_view)
            venda["deliveryMessageId"] = str(delivery_message.id)
            await run_db(save_db, db)
            return await interaction.followup.send("O pagamento já estava confirmado. Recriei o botão Enviar KEY no canal.", ephemeral=True)
        return await interaction.followup.send("O pagamento foi confirmado, mas não encontrei o canal do pedido.", ephemeral=True)
    if venda.get("status", "aguardando_pagamento") != "aguardando_pagamento":
        return await interaction.followup.send("Este pedido já foi encerrado.", ephemeral=True)
    paid_category_id = settings.get("categoriaPagoId")
    paid_category = interaction.guild.get_channel(int(paid_category_id)) if interaction.guild and paid_category_id else None
    if not isinstance(paid_category, discord.CategoryChannel):
        return await interaction.followup.send("Configure uma categoria válida para pedidos pagos no Painel do dono antes de confirmar.", ephemeral=True)
    if not isinstance(channel, discord.TextChannel):
        return await interaction.followup.send("Não encontrei o canal deste pedido. Use /desbloquear para liberar o comprador.", ephemeral=True)
    me = interaction.guild.me if interaction.guild else None
    paid_permissions = paid_category.permissions_for(me) if me else None
    if not paid_permissions or not paid_permissions.manage_channels or not paid_permissions.view_channel:
        return await interaction.followup.send("Não confirmei a operação porque o bot não tem permissão para gerenciar canais e ver a categoria de pedidos pagos. Ajuste as permissões e tente novamente; o pedido continua aguardando pagamento.", ephemeral=True)
    try:
        await channel.edit(name=f"pago-{channel.id % 10000:04d}", category=paid_category, reason="Pagamento confirmado pelo dono")
    except discord.Forbidden:
        return await interaction.followup.send("O Discord negou a mudança de categoria. O pedido continua aguardando pagamento e o canal não foi apagado. Dê ao bot permissão de gerenciar canais nas duas categorias e tente de novo.", ephemeral=True)
    except discord.HTTPException as error:
        return await interaction.followup.send(f"Não consegui mover o canal ({error}). O pedido continua aberto e não foi apagado.", ephemeral=True)
    venda["status"] = "pago"
    venda["confirmadoPor"] = str(interaction.user.id)
    venda["confirmadoEm"] = int(time.time() * 1000)
    settings["vendasPendentes"][canal_id] = venda
    sale = {"canalId": canal_id, "clienteId": venda["buyerId"], "produto": venda["produto"], "valor": venda["preco"], "confirmadoPor": str(interaction.user.id), "confirmadoEm": venda["confirmadoEm"], "status": "pago"}
    if not any(x.get("canalId") == canal_id for x in settings["vendas"]):
        settings["vendas"].append(sale)
    await run_db(save_db, db)
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(custom_id=f"enviarkey_{canal_id}", label="Enviar KEY", style=discord.ButtonStyle.success))
    view.add_item(discord.ui.Button(custom_id=f"fechar_{canal_id}", label="Fechar pedido", style=discord.ButtonStyle.danger))
    delivery_message = await channel.send("Pagamento confirmado. Quando estiver pronto, use Enviar KEY para entregar o produto ao cliente.", view=view)
    venda["deliveryMessageId"] = str(delivery_message.id)
    await run_db(save_db, db)
    try:
        original_embed = interaction.message.embeds[0].copy() if interaction.message and interaction.message.embeds else discord.Embed(title=f"Pagamento — {venda['produto']}")
        original_embed.description = (original_embed.description or "").replace("Aguardando pagamento", "Pagamento confirmado")
        original_embed.color = 0x28A745
        if interaction.message:
            await interaction.message.edit(embed=original_embed, view=None)
    except discord.HTTPException as error:
        print(f"Falha ao atualizar a mensagem do pedido {canal_id}: {error}")
    try:
        await log_sale(interaction.guild, settings, sale)
    except discord.HTTPException as error:
        print(f"Falha ao publicar o registro da venda: {error}")
    await interaction.followup.send("Pagamento confirmado. O canal foi movido para pagos e o botão de entrega está disponível.", ephemeral=True)


async def log_sale(guild, settings, sale):
    channel_id = settings.get("canalVendasId")
    if not guild or not channel_id:
        return
    channel = guild.get_channel(int(channel_id))
    if not isinstance(channel, discord.TextChannel):
        return
    embed = discord.Embed(title="Venda confirmada", color=0x168BFF)
    embed.add_field(name="Cliente", value=f"<@{sale['clienteId']}>", inline=True)
    embed.add_field(name="Produto", value=sale["produto"], inline=True)
    embed.add_field(name="Valor", value=preco_str(sale["valor"]), inline=True)
    embed.add_field(name="Confirmado por", value=f"<@{sale['confirmadoPor']}>", inline=True)
    embed.add_field(name="Horário", value=f"<t:{sale['confirmadoEm']//1000}:F>", inline=True)
    await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


async def on_botao_enviar_key(interaction: discord.Interaction, canal_id: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    db = await run_db(load_db)
    settings = db["discordSettings"]
    if not is_dono(interaction.user, db):
        return await interaction.followup.send("Você não tem permissão para enviar KEYS.", ephemeral=True)
    sale = settings["vendasPendentes"].get(canal_id)
    if not sale:
        return await interaction.followup.send("Este pedido não está mais aberto.", ephemeral=True)
    if sale.get("status") == "entregue":
        return await interaction.followup.send("A KEY deste pedido já foi enviada. O botão não pode ser usado novamente.", ephemeral=True)
    if sale.get("status") != "pago":
        return await interaction.followup.send("Confirme o pagamento antes de enviar a KEY.", ephemeral=True)
    code = sale.get("keyCode")
    key = db["keys"].get(code) if code else None
    if not key or key.get("entregueDiscord"):
        return await interaction.followup.send("Não encontrei uma KEY reservada válida para este pedido. Avise o dono antes de continuar.", ephemeral=True)
    # Persistir a trava antes da DM impede que cliques repetidos entreguem outra key.
    marcar_chave_entregue(db, code, sale["buyerId"])
    sale["status"] = "entregue"
    sale["entregueEm"] = int(time.time() * 1000)
    settings["vendasPendentes"][canal_id] = sale
    for item in settings["vendas"]:
        if item.get("canalId") == canal_id:
            item["status"] = "entregue"
    await run_db(save_db, db)
    message = f"Pagamento confirmado. Sua KEY do **{sale['produto']}** é:\n```{code}```\nUse-a em Criar conta no site para liberar o acesso."
    delivered_by_dm = False
    try:
        buyer = await bot.fetch_user(int(sale["buyerId"]))
        await buyer.send(message)
        delivered_by_dm = True
    except (discord.Forbidden, discord.HTTPException):
        pass
    except Exception:
        pass
    channel = interaction.guild.get_channel(int(canal_id)) if interaction.guild else interaction.channel
    payment_message_id = sale.get("paymentMessageId")
    if isinstance(channel, discord.TextChannel) and payment_message_id:
        try:
            payment_message = await channel.fetch_message(int(payment_message_id))
            payment_embed = payment_message.embeds[0].copy() if payment_message.embeds else discord.Embed(title=f"Pagamento — {sale['produto']}")
            payment_embed.description = (payment_embed.description or "").replace("Pagamento confirmado", "KEY enviada")
            payment_embed.color = 0x28A745
            await payment_message.edit(embed=payment_embed, view=None)
        except discord.HTTPException:
            pass
    if not delivered_by_dm and isinstance(channel, discord.TextChannel):
        await channel.send(f"<@{sale['buyerId']}> {message}", allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
    feedback = settings.get("mensagemFeedback", "Obrigado pela compra! Esperamos que aproveite. Deixe seu feedback aqui quando puder.")
    feedback = feedback.replace("{cliente}", f"<@{sale['buyerId']}>").replace("{produto}", sale["produto"])
    if isinstance(channel, discord.TextChannel):
        await channel.send(feedback, allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
        close_view = discord.ui.View(timeout=None)
        close_view.add_item(discord.ui.Button(custom_id=f"fechar_{canal_id}", label="Fechar pedido", style=discord.ButtonStyle.danger))
        if interaction.message:
            await interaction.message.edit(content="KEY enviada. O botão de entrega foi desativado. Você pode fechar o pedido quando quiser.", view=close_view)
    await interaction.followup.send("KEY enviada e registrada. A entrega não pode ser repetida.", ephemeral=True)


async def on_botao_fechar_pedido(interaction: discord.Interaction, canal_id: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    db = await run_db(load_db)
    if not is_dono(interaction.user, db):
        return await interaction.followup.send("Você não tem permissão para fechar pedidos.", ephemeral=True)
    sale = db["discordSettings"]["vendasPendentes"].get(canal_id)
    if not sale:
        return await interaction.followup.send("Este pedido já foi encerrado.", ephemeral=True)
    if sale.get("status") == "pago":
        return await interaction.followup.send("Entregue a KEY antes de fechar um pedido pago.", ephemeral=True)
    if sale.get("status") == "entregue":
        sale["canalFechadoEm"] = int(time.time() * 1000)
    else:
        sale["status"] = "cancelado"
        sale["canceladoEm"] = int(time.time() * 1000)
        key = db["keys"].get(sale.get("keyCode"))
        if key:
            key.pop("reservadaDiscord", None)
    db["discordSettings"]["vendasPendentes"].pop(canal_id, None)
    await run_db(save_db, db)
    await interaction.followup.send("Pedido encerrado. O canal será removido em alguns segundos.", ephemeral=True)
    channel = interaction.guild.get_channel(int(canal_id)) if interaction.guild else interaction.channel
    if isinstance(channel, discord.TextChannel):
        await asyncio.sleep(3)
        try: await channel.delete(reason="Pedido fechado pelo dono")
        except discord.HTTPException: pass


selected_pix_by_user = {}


async def on_select_canal_painel(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    rascunho = rascunhos_painel.get(interaction.user.id)
    if not rascunho:
        return await interaction.edit_original_response(content="Este rascunho expirou. Crie o painel novamente.", view=None)
    values = (interaction.data or {}).get("values", [])
    canal = interaction.guild.get_channel(int(values[0])) if interaction.guild and values else None
    if not isinstance(canal, discord.TextChannel):
        return await interaction.edit_original_response(content="Selecione um canal de texto válido.", view=None)
    db = await run_db(load_db)
    painel_id = "painel" + str(int(time.time() * 1000))
    rascunho["conteudo"] = db["discordSettings"].get("conteudoProduto", {"descricao": "", "imagens": []})
    db["discordSettings"]["paineis"][painel_id] = rascunho
    await run_db(save_db, db)
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(custom_id=f"buy_{painel_id}", label="Comprar agora", style=discord.ButtonStyle.success))
    view.add_item(discord.ui.Button(custom_id=f"viewproduct_{painel_id}", label="Ver produto", style=discord.ButtonStyle.secondary))
    await canal.send(embed=embed_painel(**{k: rascunho[k] for k in ("titulo", "descricao", "preco", "banner")}), view=view)
    rascunhos_painel.pop(interaction.user.id, None)
    await interaction.edit_original_response(content=f"Painel publicado em {canal.mention}.", view=None)


async def on_config_categorias(interaction: discord.Interaction):
    view = discord.ui.View(timeout=180)
    view.add_item(discord.ui.ChannelSelect(custom_id="cfg_categoria_pagamento", placeholder="Categoria de pagamentos", channel_types=[discord.ChannelType.category], row=0))
    view.add_item(discord.ui.ChannelSelect(custom_id="cfg_categoria_pago", placeholder="Categoria de pedidos pagos", channel_types=[discord.ChannelType.category], row=1))
    await interaction.response.send_message("Escolha as categorias do fluxo de pedidos.", view=view, ephemeral=True)


async def on_config_canal_vendas(interaction: discord.Interaction):
    view = discord.ui.View(timeout=180)
    view.add_item(discord.ui.ChannelSelect(custom_id="cfg_canal_vendas", placeholder="Canal privado para o registro das vendas", channel_types=[discord.ChannelType.text]))
    await interaction.response.send_message("Escolha o canal onde serão registrados cliente, produto, valor e horário das vendas.", view=view, ephemeral=True)


async def on_config_select(interaction: discord.Interaction, custom_id: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    db = await run_db(load_db)
    if not is_dono(interaction.user, db):
        return await interaction.followup.send("Você não tem permissão para alterar essas configurações.", ephemeral=True)
    values = (interaction.data or {}).get("values", [])
    if not values:
        return await interaction.followup.send("Nenhum canal foi selecionado.", ephemeral=True)
    channel_id = str(values[0])
    field, label = {
        "cfg_categoria_pagamento": ("categoriaPagamentoId", "Categoria de pagamentos"),
        "cfg_categoria_pago": ("categoriaPagoId", "Categoria de pedidos pagos"),
        "cfg_canal_vendas": ("canalVendasId", "Canal de registro de vendas"),
    }[custom_id]
    db["discordSettings"][field] = channel_id
    await run_db(save_db, db)
    selected = interaction.guild.get_channel(int(channel_id)) if interaction.guild else None
    await interaction.followup.send(f"{label} configurada: {selected.mention if selected else channel_id}.", ephemeral=True)


async def on_pix_select(interaction: discord.Interaction):
    values = (interaction.data or {}).get("values", [])
    if not values:
        return await interaction.response.send_message("Selecione uma chave Pix.", ephemeral=True)
    selected_pix_by_user[interaction.user.id] = values[0]
    await interaction.response.send_message("Chave selecionada. Escolha Definir como ativa ou Excluir selecionada.", ephemeral=True)


async def on_pix_activate(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    db = await run_db(load_db)
    if not is_dono(interaction.user, db):
        return await interaction.followup.send("Você não tem permissão para alterar as chaves Pix.", ephemeral=True)
    pix_id = selected_pix_by_user.get(interaction.user.id)
    if not pix_id or not any(x["id"] == pix_id for x in db["discordSettings"]["pixKeys"]):
        return await interaction.followup.send("Selecione uma chave Pix primeiro.", ephemeral=True)
    db["discordSettings"]["pixAtivaId"] = pix_id
    await run_db(save_db, db)
    await interaction.followup.send("Chave Pix definida como ativa.", ephemeral=True)


async def on_pix_delete(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    db = await run_db(load_db)
    if not is_dono(interaction.user, db):
        return await interaction.followup.send("Você não tem permissão para excluir chaves Pix.", ephemeral=True)
    pix_id = selected_pix_by_user.get(interaction.user.id)
    pix = next((x for x in db["discordSettings"]["pixKeys"] if x["id"] == pix_id), None)
    if not pix:
        return await interaction.followup.send("Selecione uma chave Pix primeiro.", ephemeral=True)
    view = discord.ui.View(timeout=60)
    view.add_item(discord.ui.Button(custom_id=f"pixdeleteconfirm_{pix_id}", label="Confirmar exclusão", style=discord.ButtonStyle.danger))
    await interaction.followup.send(f"Excluir a chave Pix de **{pix['nome']}**?", view=view, ephemeral=True)


async def on_pix_delete_confirm(interaction: discord.Interaction, pix_id: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    db = await run_db(load_db)
    if not is_dono(interaction.user, db):
        if interaction.message:
            await interaction.message.edit(content="Você não tem permissão para excluir chaves Pix.", view=None)
        return await interaction.followup.send("Você não tem permissão para excluir chaves Pix.", ephemeral=True)
    settings = db["discordSettings"]
    old_count = len(settings["pixKeys"])
    settings["pixKeys"] = [x for x in settings["pixKeys"] if x["id"] != pix_id]
    if len(settings["pixKeys"]) == old_count:
        if interaction.message:
            await interaction.message.edit(content="Essa chave Pix já foi removida.", view=None)
        return await interaction.followup.send("Essa chave Pix já foi removida.", ephemeral=True)
    if settings.get("pixAtivaId") == pix_id:
        settings["pixAtivaId"] = settings["pixKeys"][0]["id"] if settings["pixKeys"] else None
    selected_pix_by_user.pop(interaction.user.id, None)
    await run_db(save_db, db)
    if interaction.message:
        await interaction.message.edit(content="Chave Pix excluída.", view=None)
    await interaction.followup.send("Chave Pix excluída. Se ela era a ativa, outra chave cadastrada foi selecionada.", ephemeral=True)


# ---------------- roteador de interações (botões / select menus) ----------------

@bot.event
async def on_interaction(interaction: discord.Interaction):
    try:
        if interaction.type is not discord.InteractionType.component:
            return  # comandos de barra e modais já são tratados pelo tree/on_submit

        data = interaction.data or {}
        component_type = data.get("component_type")
        custom_id = data.get("custom_id", "")

        if component_type == 2:  # botão
            if custom_id == "pd_criar_painel":
                return await interaction.response.send_modal(ModalCriarPainel())
            if custom_id == "pd_cadastrar_pix":
                return await interaction.response.send_modal(ModalCadastrarPix())
            if custom_id == "pd_ver_pix":
                return await on_botao_ver_pix(interaction)
            if custom_id == "pd_categorias":
                if not is_dono_cached(interaction.user): return await acesso_negado(interaction)
                return await on_config_categorias(interaction)
            if custom_id == "pd_conteudo":
                if not is_dono_cached(interaction.user): return await acesso_negado(interaction)
                return await interaction.response.send_modal(ModalConteudoProduto())
            if custom_id == "pd_feedback":
                if not is_dono_cached(interaction.user): return await acesso_negado(interaction)
                return await interaction.response.send_modal(ModalFeedback())
            if custom_id == "pd_canal_vendas":
                if not is_dono_cached(interaction.user): return await acesso_negado(interaction)
                return await on_config_canal_vendas(interaction)
            if custom_id == "pix_activate":
                return await on_pix_activate(interaction)
            if custom_id == "pix_delete":
                return await on_pix_delete(interaction)
            if custom_id == "pd_enviar_painel":
                return await on_botao_enviar_painel(interaction)
            if custom_id.startswith("buy_"):
                return await on_botao_comprar(interaction, custom_id[len("buy_"):])
            if custom_id.startswith("buyno_"):
                return await on_botao_comprar_nao(interaction)
            if custom_id.startswith("buyyes_"):
                return await on_botao_comprar_sim(interaction, custom_id[len("buyyes_"):])
            if custom_id.startswith("viewproduct_"):
                return await on_botao_ver_produto(interaction, custom_id[len("viewproduct_"):])
            if custom_id.startswith("copiarpix_"):
                return await on_botao_copiar_pix(interaction, custom_id[len("copiarpix_"):])
            if custom_id.startswith("confirmar_"):
                return await on_botao_confirmar_compra(interaction, custom_id[len("confirmar_"):])
            if custom_id.startswith("enviarkey_"):
                return await on_botao_enviar_key(interaction, custom_id[len("enviarkey_"):])
            if custom_id.startswith("fechar_"):
                return await on_botao_fechar_pedido(interaction, custom_id[len("fechar_"):])
            if custom_id.startswith("pixdeleteconfirm_"):
                return await on_pix_delete_confirm(interaction, custom_id[len("pixdeleteconfirm_"):])

        elif component_type == 8:
            if custom_id == "select_canal_painel":
                if not is_dono_cached(interaction.user): return await acesso_negado(interaction)
                return await on_select_canal_painel(interaction)
            if custom_id.startswith("cfg_"):
                return await on_config_select(interaction, custom_id)
        elif component_type == 3 and custom_id == "pix_manage_select":
            if not is_dono_cached(interaction.user): return await acesso_negado(interaction)
            return await on_pix_select(interaction)

    except Exception as err:
        print("Erro numa interação:", err)
        msg = "Não consegui concluir essa ação. Confira as permissões e as configurações do bot; se continuar, envie o horário do erro ao administrador."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass


# ---------------- inicialização ----------------

@bot.event
async def on_ready():
    try:
        db = await run_db(load_db)
        role_id = db.get("discordSettings", {}).get("cargoDonoId")
        for guild in bot.guilds:
            if role_id:
                owner_role_cache[guild.id] = int(role_id)
    except Exception as err:
        print(f"Não foi possível carregar o cargo de dono em memória: {err}")
    try:
        if GUILD_OBJECT:
            bot.tree.copy_global_to(guild=GUILD_OBJECT)
            synced = await bot.tree.sync(guild=GUILD_OBJECT)
            print(f" {len(synced)} comando(s) registrados neste servidor (aparecem na hora).")
        else:
            synced = await bot.tree.sync()
            print(
                f" {len(synced)} comando(s) registrados globalmente "
                "(pode levar até 1h para aparecer)."
            )
    except Exception as err:
        print("Erro ao sincronizar comandos:", err)

    print(f" Bot online como {bot.user}")


if __name__ == "__main__":
    bot.run(os.environ.get("DISCORD_TOKEN"))

