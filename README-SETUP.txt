PASSOS:
1. No GitHub, crie o secret DISCORD_WEBHOOK_URL com o link do webhook do Discord.
2. No repositório, crie o ficheiro monitor_confibor.py e cole o conteúdo deste template.
3. Crie o ficheiro .github/workflows/confibor-monitor.yml e cole o conteúdo deste template.
4. Em Settings > Actions > General > Workflow permissions, escolha Read and write permissions.
5. Vá a Actions e execute o workflow "Confibor monitor" manualmente pela primeira vez.
6. Na primeira execução ele grava o estado atual e não envia alerta.
7. Nas seguintes, avisa no Discord quando houver produto novo ou reposição.
