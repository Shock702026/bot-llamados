# Bot de llamados

Todos los días a las 9:00 (hora de Uruguay) revisa Uruguay Concursa y manda a Telegram
los llamados nuevos de comunicación en Montevideo y Maldonado.

- `bot.py`: el programa.
- `config.yaml`: las palabras clave y los departamentos. Se puede editar desde GitHub.
- `seen.json`: lo crea el bot solo; es su memoria de llamados ya revisados. Si lo borrás,
  la próxima vez te vuelve a mandar todos los abiertos.
- `.github/workflows/bot.yml`: el horario.

Para probarlo a mano: pestaña Actions → Bot de llamados → Run workflow.
