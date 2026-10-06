# FROST SENSI — Bot de Discord (versão Python)

Bot para cadastrar/gerar chaves e vender via Discord com Pix estático. O
pagamento é confirmado manualmente pelo dono do servidor.

Esta é a versão em **Python (discord.py)** do bot, com o mesmo comportamento
da versão em Node.js: mesmos comandos, mesmo painel do dono, mesmo fluxo de
compra e o **mesmo banco de dados (Supabase)** que o site já usa — chaves
cadastradas aqui aparecem no site também, e vice-versa.

## 1. Criar o bot no Discord

1. Vá em https://discord.com/developers/applications → **New Application**.
2. Dê um nome (ex: FROST SENSI) → **Bot** (menu lateral) → **Reset Token** → copie o token.
3. Ainda em **Bot**, ative (se pedir) as opções de intents — não precisa nenhuma especial pra esse bot.
4. Em **OAuth2 > URL Generator**: marque `bot` e `applications.commands`. Em
   permissões, marque pelo menos: `Manage Channels`, `Send Messages`,
   `Embed Links`, `Attach Files`, `Read Message History`, `Use Slash Commands`.
5. Abra o link gerado e adicione o bot no seu servidor.
6. Pegue o **Application ID** (em General Information) e o **ID do seu servidor**
   (ative o Modo Desenvolvedor no Discord: Config > Avançado > Modo
   desenvolvedor; depois clique com botão direito no servidor > Copiar ID).

## 2. Configurar o projeto

1. Copie `_env.example` para um arquivo chamado `.env`.
2. Preencha:
   - `DISCORD_TOKEN` = o token do passo 1
   - `GUILD_ID` = o ID do seu servidor (assim os comandos aparecem na hora)
   - `SUPABASE_URL` e `SUPABASE_KEY` já vêm preenchidos (mesmo banco do site).
   - `CLIENT_ID` não é usado pelo código Python (o discord.py descobre isso
     sozinho a partir do token), mas pode deixar preenchido por referência.

3. Crie um ambiente virtual, instale as dependências e rode o bot:
   ```bash
   python -m venv venv
   source venv/bin/activate   # no Windows: venv\Scripts\activate
   pip install -r requirements.txt
   python main.py
   ```

Os comandos de barra (`/`) são registrados automaticamente sempre que o bot
liga — não existe um passo separado de "deploy-commands" como na versão em
Node: se `GUILD_ID` estiver preenchido no `.env`, os comandos aparecem no
servidor na hora; se estiver vazio, aparecem globalmente (pode levar até 1h).

## 3. Colocar pra rodar 24h (Railway / Render / Replit)

Suba essa pasta inteira num repositório do GitHub, depois:

- **Railway**: New Project > Deploy from GitHub repo > adicione as variáveis
  de ambiente (as mesmas do `.env`) em Variables. Railway detecta o
  `requirements.txt` automaticamente; defina o comando de start como
  `python main.py` (em Settings > Deploy).
- **Render**: New > Background Worker > conecte o repositório > Build
  Command: `pip install -r requirements.txt` > Start Command: `python main.py`
  > adicione as variáveis de ambiente em Environment.
- **Replit**: importe o repositório, adicione as variáveis em "Secrets", e
  use o botão Run (ou configure um "Always On"/Deployment pra ficar 24h).

## 4. Primeiros passos no servidor

1. `/configurar-cargo-dono cargo:@Dono` — defina o cargo que pode usar o
   painel (você e o Mikael, por exemplo).
2. `/cadastrar-chaves` — cola as chaves separadas por vírgula, ou
   `/gerar-chaves quantidade:100` pra já criar 100 novas automaticamente.
3. `/painel-dono` — abre o painel de administração:
   - **Criar painel** → título, descrição, preço e banner; depois selecione o canal.
   - **Chave Pix** e **Ver Pix cadastrados** → cadastrar, ativar ou excluir chaves.
   - **Categorias** → escolha as categorias de pagamentos e de pedidos pagos.
   - **Conteúdo do produto** → configure uma descrição e até três imagens para **Ver produto**.
   - **Mensagem de feedback** → personalize o agradecimento enviado após a entrega.
   - **Canal de vendas** → registre cliente, produto, valor, horário e responsável.
   - `/desbloquear pessoa:@Pessoa` → cancela um pedido ainda pendente e libera a pessoa para tentar comprar novamente. Pedidos pagos com canal ativo precisam ser finalizados primeiro.

## 5. Como funciona a venda

1. Cliente clica **Comprar agora**, confere o preço configurado e confirma.
2. O bot impede outro pedido enquanto o cliente tiver um pedido ativo e cria
   um canal privado para cliente, bot e cargo de dono.
3. O canal mostra o QR Code Pix e **Copiar Pix**. A equipe confere o recebimento.
4. O dono clica **Confirmar pagamento**. O bot atualiza o status, move o canal
   para a categoria de pagos e registra a venda.
5. O dono clica **Enviar KEY**. A key é enviada por DM; se a DM estiver fechada,
   é enviada no canal privado. O botão é desativado depois da entrega.
6. O bot publica a mensagem de agradecimento/feedback configurada. O dono pode
   fechar o pedido pelo botão no canal.

O painel público tem somente **Comprar agora** e **Ver produto**. O segundo
envia uma mensagem privada com a descrição e até três imagens.

## Observações importantes

- **Confirmação de pagamento é manual.** Como é Pix estático (sem gateway),
  só você sabe se o dinheiro realmente caiu — por isso o botão "Confirmar
  pagamento" existe: só clique quando vir o Pix na sua conta.
- Se quiser confirmação **automática** no futuro, dá pra trocar por um
  gateway de pagamento (Mercado Pago, Efí, etc.) — aí precisaria de conta
  nesse serviço e de outra chave de API.
- As chaves entregues pelo bot continuam com status "disponível" no banco
  até o cliente realmente criar a conta no site com ela — assim elas
  funcionam normalmente lá também.
- Configure as duas categorias antes de abrir o painel de vendas. O bot exige
  uma categoria de pagamentos e outra de pedidos pagos. Dê ao bot permissão
  para ver canais, enviar mensagens e gerenciar canais nas duas categorias.
- Configure o cargo de dono com `/configurar-cargo-dono` para que o bot mencione
  a equipe responsável e dê acesso aos canais privados de pedido.
- O cadastro/remoção de uma chave Pix afeta novas cobranças. Pedidos já abertos
  continuam com o código Pix gerado quando foram criados.
- Os "rascunhos" de painel (entre criar e enviar) ficam só na memória do
  bot, igual na versão Node: se o bot reiniciar no meio da criação de um
  painel, é só criar de novo.

## Diferenças em relação à versão Node.js

O comportamento é o mesmo; só a implementação muda:
- `discord.js` → `discord.py` (comandos de barra via `app_commands`, botões
  e select menus roteados pelo `custom_id`, exatamente como na versão Node).
- `@supabase/supabase-js` → pacote `supabase` (cliente síncrono, chamado por
  uma thread separada pra não travar o bot).
- `qrcode` (Node) → `qrcode` (Python, com `Pillow` para gerar o PNG).
- Não existe um script `deploy-commands` separado: a sincronização dos
  comandos de barra acontece automaticamente toda vez que o bot liga.
