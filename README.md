# Bot de llamados

Todos los días a las 9:00 (hora de Uruguay) revisa tres páginas y manda a Telegram
los llamados nuevos de comunicación en Montevideo y Maldonado:

- **Uruguay Concursa**: filtra con tus palabras clave y departamentos.
- **Intendencia de Montevideo** (ofertalaboral.montevideo.gub.uy): filtra con tus palabras clave.
- **Intendencia de Maldonado** (maldonado.gub.uy/Llamados_Laborales): avisa **todos** los llamados nuevos,
  porque publican muy pocos.

Si una página no responde, el bot te avisa y manda igual lo de las otras.

- `bot.py`: el programa.
- `config.yaml`: las palabras clave y los departamentos. Se puede editar desde GitHub.
- `seen.json`: lo crea el bot solo; es su memoria de llamados ya revisados. Si lo borrás,
  la próxima vez te vuelve a mandar todos los abiertos.
- `.github/workflows/bot.yml`: el horario.

Para probarlo a mano: pestaña Actions → Bot de llamados → Run workflow.
