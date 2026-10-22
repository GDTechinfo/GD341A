# CH341 Studio

Software moderno e open source para o gravador **CH341A** — Windows e Linux.

**Criado por GDTech Informática** · [YouTube](https://youtube.com/GDTechinformatica) · [Instagram](https://instagram.com/thegregin)

> 🇺🇸 *Modern open-source software for the CH341A programmer (SPI 25xx flash and I²C 24xx EEPROM), for Windows and Linux. The interface is available in Portuguese and English (Tools → Language).*

## Download

Baixe a versão mais recente na página **[Releases](../../releases)**:

- **Windows:** `CH341Studio-<versão>-instalador-windows.exe`. Execute e siga o instalador; ele cria o atalho na área de trabalho.
- **Linux:** `CH341Studio-<versão>-x86_64.AppImage`. Um arquivo só, sem instalar: clique com o botão direito → Propriedades → marque **"Permitir executar como programa"** (ou `chmod +x` no terminal) e abra com dois cliques. Na primeira vez, o programa oferece criar o atalho na área de trabalho e no menu e instalar a regra USB do CH341A.

Depois de instalar, escolha o driver (veja [Instalação](#instalação)) e clique em **Conectar**. Sem gravador? Use o modo **Simulador** para conhecer o programa.

> ⚠️ Gravar memória sempre tem risco. Faça backup (leia e salve o conteúdo original) antes de gravar. Memórias de **1,8 V** precisam do adaptador de 1,8 V.

## O que ele faz

- **Memórias SPI 25xx** (BIOS, roteadores, TVs, monitores): detectar pelo JEDEC ID, ler, gravar, verificar, apagar, teste de branco.
- **EEPROMs I²C 24xx** (24C01 até 24C1024): ler, gravar, verificar, apagar, com escolha da velocidade (20/100/400/750 kHz).
- **Gravação inteligente:** lê o chip, compara e só apaga/grava os setores que mudaram. Uma alteração pequena numa BIOS de 8 MB leva segundos, não minutos.
- **Dois drivers:**
  - **libusb** (Linux, e Windows com WinUSB).
  - **CH341DLL** oficial da WCH (Windows), que convive com NeoProgrammer/AsProgrammer.
  - Modo **Automático** que tenta os dois.
- **Proteção de escrita:**
  - Mostra SR1/SR2 e remove os bits BP.
  - Preserva o bit **QE**: não estraga BIOS que usam leitura quad.
  - Trata as particularidades da Macronix e das SST25/SST26.
- **Memórias acima de 16 MB** (W25Q256, MX25L256…): endereçamento de 4 bytes automático.
- **Aviso de memória 1,8 V** em destaque (W25Q..FW, MX25U, GD25LQ, IS25WP).
- **Editor hexadecimal** rápido:
  - Abre imagens grandes sem travar.
  - Permite edição direta, "Ir para" (Ctrl+G) e busca hex/texto (Ctrl+F, F3).
- **Comparar com arquivo:** os bytes diferentes ficam em vermelho e as faixas aparecem no log.
- **Arquivos e checksums:**
  - Lê e salva .bin e Intel HEX, com CRC32 e soma de 16 bits.
  - Arrastar e soltar arquivos na janela.
  - A leitura só carrega os dados no buffer; Salvar/Salvar como sempre pergunta a pasta e o nome.
- **Operação:**
  - Progresso com velocidade (KiB/s) e tempo restante; cancelar a qualquer momento.
  - Tema escuro; atalhos F5 detectar, F6 ler, F7 gravar, F8 verificar.
- **Simulador** embutido para testar e demonstrar sem gravador (ótimo para gravar vídeo).
- **Linha de comando** completa, para scripts.

## Instalação

Requer Python 3.10 ou mais novo.

```bash
pip install -r requirements.txt
python run.py            # abre a interface
```

### Windows: escolha um dos drivers

| Modo | Driver do CH341A | Como preparar |
|---|---|---|
| **CH341DLL** | Oficial WCH (CH341PAR) | Instale o CH341PAR da WCH e copie `CH341DLLA64.DLL` (Python 64 bits) para a pasta do programa ou para a subpasta `drivers/` |
| **libusb** | WinUSB | Abra o [Zadig](https://zadig.akeo.ie), selecione o dispositivo `USB-EPP/I2C... CH341A` (1A86:5512) e instale o **WinUSB** |

O WinUSB substitui o driver oficial. Se quiser voltar a usar outros programas com o driver da WCH, reinstale o CH341PAR. O modo **Automático** tenta a DLL primeiro e depois o libusb.

### Linux

```bash
sudo apt install libusb-1.0-0          # (ou o equivalente da sua distro)
sudo cp linux/99-ch341a.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Depois reconecte o gravador.

## Instaladores (com atalho na área de trabalho)

**Windows** — `build_instalador_windows.bat`
- Precisa do Python 64 bits (python.org) e do **Inno Setup 6** (gratuito: https://jrsoftware.org/isdl.php).
- Gera `dist\CH341Studio-<versão>-instalador-windows.exe`.
- O instalador:
  - é em português, com ícone próprio;
  - instala sem pedir administrador (ou para todos os usuários, se você escolher);
  - cria atalho na área de trabalho (opção marcada por padrão) e no menu Iniciar;
  - aparece em "Aplicativos instalados", para desinstalar pelo Windows.

**Linux (AppImage)** — `./build_appimage.sh`
- Gera `dist/CH341Studio-<versão>-x86_64.AppImage`, um arquivo único que roda na maioria das distros, sem instalar nada.
- Na primeira abertura, o programa oferece criar o atalho na área de trabalho e no menu (apontando para o AppImage) e instalar a regra USB.
- Se você mover o arquivo de pasta, o atalho é corrigido sozinho na próxima abertura.
- Os mesmos itens ficam em **Ferramentas → Atalho na área de trabalho e no menu (Linux)** e **Ferramentas → Instalar regra USB do CH341A (Linux)**.
- Se o AppImage não abrir numa distro sem FUSE: `./CH341Studio-*.AppImage --appimage-extract-and-run`.

**Linux (pacote com instalador, alternativa)** — `./build_instalador_linux.sh`
- Gera `dist/CH341Studio-<versão>-linux-x86_64.tar.gz`. Quem recebe faz `tar xzf ...`, entra na pasta e roda `./instalar.sh`.
- O instalador:
  - instala em `~/.local/share/ch341studio` (sem senha), ou em `/opt` com `--sistema`;
  - cria o atalho no menu de aplicativos e na área de trabalho (inclusive "Área de Trabalho" em português, e já marcado como confiável no GNOME);
  - cria o comando `ch341studio` no terminal;
  - oferece instalar a regra USB do CH341A.
- Para remover: `desinstalar.sh`.
- Nos dois casos, gere numa distro mais antiga que as dos usuários (por exemplo Ubuntu 22.04), porque o executável depende da versão da glibc de onde foi gerado. O GitHub Actions já faz isso.

## Gerar o executável

- Windows: `build_windows.bat` gera `dist\CH341Studio\CH341Studio.exe`. Use sempre o .exe da pasta **dist**; para outro PC, copie a pasta `dist\CH341Studio` inteira (o .exe sozinho não abre). Instale o Python pelo **python.org**: o da Microsoft Store pode gerar executáveis que não abrem.
- Linux: `./build_linux.sh` gera `dist/CH341Studio/CH341Studio`.

Para incluir a DLL oficial no pacote do Windows, coloque `CH341DLLA64.DLL` em `drivers/` antes de compilar.

**Versão portátil (pendrive):** `build_windows_portable.bat` gera `dist\CH341Studio-portable\` com um único `.exe` e um `portable.txt`. Com esse arquivo ao lado do programa, configurações, histórico e banco de chips ficam na pasta `dados` ali mesmo, sem nada no computador. Apague o `portable.txt` para voltar ao modo normal.

## Linha de comando

```bash
python -m ch341studio detect
python -m ch341studio read backup.bin
python -m ch341studio write bios.bin --unprotect      # gravação inteligente + verificação
python -m ch341studio write bios.bin --full           # apaga tudo e grava tudo
python -m ch341studio verify bios.bin
python -m ch341studio erase
python -m ch341studio status                          # SR1/SR2 e proteção
python -m ch341studio --i2c 24C02 read eeprom.bin
python -m ch341studio -b sim read teste.bin           # simulador, sem hardware
python -m ch341studio chips                           # lista o banco de chips
python -m ch341studio write bios.bin --turbo          # gravação turbo (libusb)
python -m ch341studio clip-test                       # estabilidade do contato
python -m ch341studio benchmark --write               # velocidade (área de teste restaurada)
python -m ch341studio analyze dump.bin                # saúde, conteúdo e regiões de um arquivo
```

Use `-b libusb` ou `-b dll` para forçar um driver.

## Observações sobre o hardware

- O CH341A **preto original** usa nível lógico de 5 V nos dados. Com o **mod para 3,3 V** ele fica seguro para memórias 3,3 V.
- Memórias de **1,8 V** sempre precisam do adaptador 1,8 V. O programa avisa quando detecta uma.
- Gravando na placa (com clipe), desligue a fonte da placa. Se a leitura vier toda 0x00/0xFF ou o ID não bater, o clipe está mal encaixado ou outro chip da placa está "puxando" o barramento.

## Ferramentas (menu Ferramentas ▾)

**Contra mau contato**
- **Ler duas vezes e comparar** (ligado por padrão): se as leituras não baterem, o programa avisa, marca os bytes em vermelho e registra "leituras diferentes" no histórico.
- **Análise automática de toda leitura:** detecta dump espelhado (clipe lendo errado ou chip menor), bit travado em 0 ou 1, tudo 0x00 e padrão repetido (chip não respondeu).
- **Testar o clipe:** lê o ID e o início do chip 20 vezes e diz se o contato está estável.

**BIOS de notebook e placa-mãe**
- **Analisar dump:** identifica o conteúdo (Intel com descritor, AMD PSP, AMI Aptio, Insyde, Phoenix, U-Boot, TV MStar, EDID…), mostra o ID e a versão da BIOS e os textos de modelo/versão encontrados.
- **Regiões Intel:** mostra Descriptor / BIOS / ME / GbE. Dá para extrair uma região, substituí-la por um arquivo e **gravar só uma região** (por exemplo, atualizar a BIOS sem tocar no ME e no MAC).
- **Remover cabeçalho .CAP:** ao abrir um .CAP maior que o chip, o programa oferece remover o cabeçalho (cápsulas UEFI/AMI, como as da ASUS).
- **Transplante de dados da placa (assistente):** compara o dump original com a BIOS limpa e lista o que pode ser levado:
  - região GbE (MAC);
  - chave do Windows (tabela MSDM);
  - área DMI `$DMI` (serial/UUID em AMI antigas);
  - volumes NVRAM.

  Você marca o que quer. Os itens arriscados (NVRAM) vêm desmarcados. O resultado é salvo num **arquivo novo** e nada é gravado no chip. Também procura textos (como o serial da etiqueta) nos dois arquivos e sugere possíveis seriais.

**Gravação**
- **Gravação turbo** (libusb): grava em lotes, sem consultar o chip a cada página. Cada lote é conferido; páginas que falharem são regravadas do jeito normal, e a espera aumenta sozinha em chips mais lentos.
- **Benchmark:** mede leitura e gravação normal × turbo. A gravação usa uma área de 64 KiB no fim do chip, que é copiada antes e restaurada e conferida no final.
- **Backup do chip antes de gravar** (opcional, desligado por padrão): salva o conteúdo atual numa pasta escolhida por você.

**Pesquisa e avisos**
- **Pesquisar (Ctrl+F):** procura um texto no buffer (por exemplo `version` numa BIOS de placa de vídeo) e lista todas as ocorrências com o texto da frente, como `Version 86.04.3B.00.25`. Por padrão não diferencia maiúsculas e também acha texto em UTF-16. Clique numa linha para ir até ela (o trecho fica destacado); F3 vai para a próxima. Também pesquisa em hex.
- **Avisar quando terminar:** ao terminar de ler, gravar, verificar, apagar, desproteger, fazer teste de branco, teste do clipe ou benchmark, aparece uma janela no centro da tela (com bipe, e piscando na barra de tarefas se o programa estiver minimizado). Liga e desliga pela opção "Avisar quando terminar" no painel de operações. Se der problema, aparece só o aviso do problema.

**Organização**
- **Histórico de serviços:** toda leitura, gravação, verificação e apagamento fica registrada com chip, ID, CRC32, SHA-256, resultado, arquivo e o campo "Serviço / cliente / placa". Tem busca, nota editável e duplo clique para abrir o arquivo.
- **Comparação visual:** mapa colorido dos blocos diferentes entre o buffer e um arquivo; clique num bloco para ir até ele.
- **Editor de EDID** (24C02 de monitor/TV): fabricante, produto, série, nome, data, e corrige o checksum sozinho.
- **Idioma:** português ou inglês (Ferramentas → Idioma; reinicie o programa).

## Atualização automática do banco de chips

Além do banco embutido, o programa baixa e converte três bases públicas:

- **flashrom** (`flashchips/*.c`), a mais revisada;
- **IMSProg** (`IMSProg.Dat`), feita para o CH341A;
- **AsProgrammer** (`chiplist.xml`).

Juntas somam mais de 1.100 memórias SPI e cerca de 75 EEPROMs I²C.

- **Automático:** com "Atualizar sozinho (1x/dia)" marcado, ao abrir o programa ele verifica as fontes em segundo plano. Chips novos entram na lista e na detecção na hora, e o log mostra os nomes.
- **Manual:** botão **Atualizar agora**, ou `python -m ch341studio update-chips` na linha de comando.
- **Prioridade:** seu arquivo `*_user.json` vem primeiro, depois o banco embutido, depois o automático. Entre as fontes automáticas, a ordem de confiança é flashrom, IMSProg e AsProgrammer.
- **Segurança:**
  - As flags de cuidado (SR2/QE, programação byte a byte, desbloqueio SST) somam as de todas as fontes.
  - Na dúvida sobre tensão, o chip é marcado como 1,8 V e o programa avisa.
- **Sem internet:** o programa continua com a última versão baixada.
- **Fonte de cada chip:** aparece no campo "Fonte" do painel. Os dados ficam em `chips_auto.json`, na pasta de configuração.
- **Licença:** as bases são baixadas no seu computador e não são distribuídas com o programa. flashrom e IMSProg são GPL; o código do CH341 Studio continua MIT.

## Adicionar memórias

O banco fica em `ch341studio/chips/spi_flash.json` e `i2c_eeprom.json`. Para incluir chips sem mexer no programa, crie um arquivo com o mesmo formato:

- Windows: `%APPDATA%\CH341Studio\spi_flash_user.json`
- Linux: `~/.config/ch341studio/spi_flash_user.json`

```json
{"chips": [
  {"name": "XT25F128B", "vendor": "XTX", "id": "0B4018", "size": 16384, "volt": "3.3", "flags": ["sr2"]}
]}
```

Se o ID não estiver no banco, o programa ainda monta um perfil genérico pelo byte de capacidade e avisa.

## Estrutura

```
ch341studio/
  protocol.py           comandos USB do CH341A (SPI/I2C/UIO stream)
  backends/
    libusb_backend.py   driver libusb (escrita e leitura simultâneas para velocidade)
    dll_backend.py      driver CH341DLL oficial (ctypes)
    simulator.py        CH341A + W25Qxx + 24Cxx simulados no nível dos pacotes USB
  spiflash.py           operações SPI 25xx (inteligente, turbo, 4 bytes, proteção)
  eeprom24.py           operações I2C 24xx
  health.py             detecção de mau contato e dumps suspeitos, teste do clipe
  identify.py           identificação do conteúdo do dump
  intel.py              descritor Intel (regiões) e cápsulas .CAP
  transplant.py         assistente de transplante (MAC, MSDM, NVRAM, DMI)
  edid.py               leitura/edição de EDID
  benchmark.py          medição de velocidade
  history.py            histórico de serviços (SQLite)
  chipupdate.py         atualização automática do banco de chips
  i18n.py, i18n_en.py   tradução (português/inglês)
  chipdb.py, chips/     banco de memórias
  fileio.py             .bin/.hex, checksums, comparação
  cli.py                linha de comando
  gui/                  interface PySide6 (janela, editor hex, ferramentas, tema)
tests/                  testes automáticos (rodam no simulador)
```

Testes: `python -m unittest discover -s tests -v`

## Próximos passos sugeridos

- EEPROMs Microwire 93Cxx, SPI 25xx (25LC/25AA) e 95xx.
- Modo de exibição para transmissões (OBS).
- Suporte ao CH347.
- Tradução em inglês das mensagens de erro do núcleo (hoje a interface está traduzida; algumas mensagens técnicas ainda aparecem em português).

## Contribuir

- **Problemas e sugestões:** abra uma [issue](../../issues) com o modelo da memória, o driver usado e as linhas do log.
- **Código:** faça um fork, crie um branch e abra um pull request. Rode os testes antes: `python -m unittest discover -s tests`.
- **Novas memórias:** pull requests no `ch341studio/chips/spi_flash.json` são bem-vindos (informe o datasheet).

## Licença

MIT — veja `LICENSE`.
