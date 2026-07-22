# USER STORIES — El dashboard de Sofía

> Las secciones del panel, contadas desde la silla del **dueño de la clínica**. No
> es quien construyó el sistema: es quien lo abre en la mañana con un café para
> saber si valió la pena pagarlo. Cada historia trae su criterio de aceptación
> —cómo sabemos que quedó bien— y el porqué detrás.
>
> Las historias 1–5 y 10–11 son de lectura y acciones puntuales; **las 6–9 son el
> salto a panel de control**: el dueño cambia cómo suena y se comporta Sofía, y esos
> cambios se publican a las llamadas en vivo.

El personaje: **el Dr. o la dueña de "Clínica Dental Sonrisa Perfecta".** No es
técnico. No quiere entrar a Retell, ni a GoHighLevel, ni a leer un log. Quiere una
página que le diga, de un vistazo, si Sofía trabajó —y poder ajustarla sin llamar a
nadie.

---

## 1. Métricas del periodo

> **Como** dueña de la clínica,
> **quiero** ver de un vistazo cuántas llamadas contestó Sofía, cuántas citas
> agendó, su tasa de éxito y cuánto duran en promedio,
> **para** saber en diez segundos si el agente está trabajando, sin abrir ningún
> otro sistema.

**Criterios de aceptación:**

- Cuatro tarjetas arriba de todo: llamadas totales, citas agendadas por Sofía, tasa
  de éxito, duración promedio.
- La tarjeta de citas dice **"Citas agendadas por Sofía"**, no "citas" a secas —
  cuenta solo lo que agendó el agente, no lo que la recepcionista puso a mano.
- Si en el periodo no hubo llamadas, la tasa de éxito muestra un guion y "Sin
  llamadas en este periodo", **nunca 0%**. Un 0% se lee como fracaso; "no hubo
  llamadas" no es fracaso.
- Si Retell no responde, la sección dice "Dato no disponible" con el motivo, **no un
  cero**.

**Por qué importa:** esta fila es el argumento de venta del mantenimiento mensual.
Es lo primero que ve, y tiene que ser cierto.

---

## 2. Temperatura de pacientes

> **Como** dueña,
> **quiero** ver cuántos pacientes están calientes, tibios o fríos,
> **para** saber a cuántos vale la pena que mi equipo llame hoy.

**Criterios de aceptación:**

- Tres conteos: caliente / tibio / frío, con un color por cada uno.
- Los conteos salen de las etiquetas que el análisis post-llamada escribió en cada
  contacto de GHL —no es una estimación del panel—.
- Cada categoría dice en una línea qué significa ("urgencia o interés alto", "solo
  preguntaba").

**Por qué importa:** le dice al equipo humano dónde poner su tiempo. Un lead
caliente que nadie llama es dinero en la mesa.

---

## 3. Pipeline (funnel)

> **Como** dueña,
> **quiero** ver en qué etapa va cada paciente —del primer contacto a cliente
> ganado—,
> **para** entender dónde se me atoran y cuántos van avanzando.

**Criterios de aceptación:**

- Una barra por etapa del pipeline "Nuevos Pacientes", en el mismo orden que en GHL.
- El largo de cada barra es comparable de un vistazo; el conteo exacto va al lado.
- Si aparecen oportunidades en una etapa que el panel no reconoce (porque alguien
  cambió el pipeline en GHL), lo dice explícito en vez de esconderlas —si no, los
  números no cuadran y parece un bug—.

**Por qué importa:** es la foto del negocio, no solo del agente. Muestra que Sofía
alimenta un embudo real, no solo que contesta el teléfono.

---

## 4. Llamadas recientes

> **Como** dueña,
> **quiero** ver la lista de las últimas llamadas con quién llamó, cuándo, cuánto
> duró, si agendó y un resumen,
> **y poder abrir una** para leer la conversación completa,
> **para** revisar una llamada puntual sin entrar a Retell.

**Criterios de aceptación:**

- Tabla con: paciente, fecha, origen, duración, resultado (agendó / no agendó),
  urgencia, resumen.
- Un renglón se abre y muestra el resumen completo, los scores (interés,
  probabilidad de asistir), qué hizo el sistema (qué tools se dispararon), la
  transcripción entera, y —cuando existe— la grabación (ver historia 8).
- Una llamada **sin teléfono** —una web call, o alguien que colgó antes de dar sus
  datos— aparece como "Sin identificar", no como un renglón roto ni vacío.
- Si GHL está caído pero Retell no, la tabla igual muestra las llamadas y sus
  duraciones, con un aviso de que faltan nombres y resúmenes. Una tabla parcial es
  más útil que una página de error.
- **El expediente clínico del doctor NUNCA aparece aquí.** Ese campo es registro
  médico y el panel no lo lee ni lo muestra, ni siquiera cuando trae el resto del
  contacto.

**Por qué importa:** es donde el dueño verifica que Sofía suena bien y no promete
cosas raras. La confianza en el agente se construye pudiendo leer lo que dijo.

---

## 5. Editar cómo habla Sofía

> **Como** dueña,
> **quiero** cambiar el guion de Sofía —un precio, una promoción, cómo saluda—
> desde el panel,
> **para** ajustar el agente sin depender de nadie ni entrar a un sistema técnico.

**Criterios de aceptación:**

- Un editor de texto con el prompt vigente de Sofía y un botón de guardar.
- Guardar escribe directo a Retell; el cambio es inmediato en la siguiente llamada.
- **Las reglas de seguridad no se pueden editar.** El bloque que impide que Sofía dé
  diagnósticos o confirme citas falsas aparece como un marcador no editable; abajo se
  puede leer completo, en solo lectura.
- Si el cliente borra ese marcador e intenta guardar, **el guardado se rechaza** con
  un mensaje que explica por qué, y un botón para restaurar el bloque. No es una
  advertencia que se pueda ignorar: no deja guardar.
- Hay un botón "Restaurar versión anterior" que revierte al prompt previo en un clic.

**Por qué importa:** este es el argumento de negocio del mantenimiento mensual —"nunca
entras a Retell"—. Y la protección de las reglas es lo que evita que un cliente, sin
querer, convierta a su recepcionista en un riesgo legal.

---

## 6. Cambiar la voz de Sofía

> **Como** dueña,
> **quiero** elegir cómo suena la voz de Sofía —cuál voz, qué tan rápido habla, cuánta
> emoción pone—,
> **para** que suene como quiero que suene mi clínica, sin pedirle nada a nadie.

**Criterios de aceptación:**

- Un menú con **voces ya elegidas** (femeninas, español de México); no un catálogo
  interminable donde equivocarse. La voz actual está en la lista.
- Un control de **velocidad** que no puede pasarse de lento ni de rápido —el panel no
  deja salirse del rango que suena bien—.
- Un **interruptor de expresividad**: más cálida o más neutra.
- Al guardar, **el cambio se aplica a las llamadas de inmediato** —entrantes y
  salientes—, sin que la dueña tenga que publicar nada ni entender de versiones.
- Un botón **"Llámame para probar"** (historia 9) para oír el resultado en segundos.

**Por qué importa:** la voz es la primera impresión de la clínica. Poder ajustarla
sola, en un minuto, es lo que hace sentir el panel como un producto y no como una
demo. Y como las opciones están acotadas, **no hay forma de romper a Sofía** eligiendo
algo raro.

---

## 7. Ajustar el comportamiento de Sofía

> **Como** dueña,
> **quiero** decir qué tan apegada al guion está Sofía —más estricta o más
> conversacional—,
> **para** afinar su tono sin tocar configuraciones técnicas que no entiendo.

**Criterios de aceptación:**

- Tres opciones con nombre claro: **Estricta**, **Balanceada**, **Flexible** —nunca un
  número ni jerga—.
- Cada una explica en una línea qué esperar ("se ciñe al guion" / "natural pero
  enfocada" / "más espontánea").
- Al guardar, se aplica en vivo a los dos agentes, igual que la voz.
- Por diseño, **ninguna opción puede volver a Sofía impredecible**: incluso la más
  flexible está dentro de un límite seguro.

**Por qué importa:** deja al dueño encontrar el tono de su clínica —una odontopediatría
quiere cálida y suelta; una de alta especialidad, seca y precisa— sin arriesgar que
Sofía empiece a improvisar de más.

---

## 8. Escuchar la grabación de una llamada

> **Como** dueña,
> **quiero** reproducir el audio de una llamada, no solo leer la transcripción,
> **para** oír con mis propios oídos cómo trató Sofía a un paciente.

**Criterios de aceptación:**

- Dentro del detalle de una llamada, un **reproductor de audio** cuando esa llamada
  tiene grabación.
- El audio se trata como **información privada del paciente**: solo se puede
  reproducir con la sesión del panel abierta; nunca queda expuesto en una dirección
  pública que alguien pueda compartir.
- Un aviso de que es audio real de un paciente.

**Por qué importa:** leer una transcripción no transmite el tono. Oír la llamada es
lo que le da al dueño la confianza de que Sofía suena como una recepcionista de
verdad. Y como es la voz de un paciente real, **tiene que estar protegida** —no basta
con esconder el enlace—.

---

## 9. "Llámame para probar"

> **Como** dueña,
> **quiero** que Sofía me llame a mí ahora mismo con la configuración que acabo de
> guardar,
> **para** escuchar los cambios en una llamada real, no imaginármelos.

**Criterios de aceptación:**

- Un campo para mi número y un botón "Llámame para probar".
- Sofía marca de inmediato con la **voz y el comportamiento actuales** —lo que se
  acaba de aplicar—.
- Recuerda mi número para la próxima vez (sin que el sistema lo guarde como dato de
  la clínica).
- Avisa que consume unos centavos de minutos de salida.

**Por qué importa:** cierra el ciclo. Cambio la voz → me llamo → la oigo → ajusto.
Sin esto, el dueño edita a ciegas y no sabe si quedó como quería hasta que llama un
paciente real.

---

## 10. Llamar a un paciente

> **Como** dueña,
> **quiero** escribir un número y que Sofía llame ahora mismo,
> **para** recuperar a un paciente sin esperar al ciclo automático.

**Criterios de aceptación:**

- Un campo de teléfono y un botón "Llamar ahora".
- El número se valida; si está mal escrito, lo dice antes de intentar.
- El botón muestra estado claro ("Llamando…", "Llamada iniciada") y no dispara dos
  veces mientras la primera está en curso.
- Advierte que va a marcar de inmediato a un número real.

**Por qué importa:** es la única acción del panel que toca el mundo real —hace sonar
un teléfono—, así que tiene que ser deliberada y clara.

---

## 11. Estado del sistema

> **Como** dueña,
> **quiero** ver si las piezas del sistema están funcionando,
> **para** que cuando un número se vea raro, la primera pregunta —"¿algo está
> caído?"— tenga respuesta en la misma pantalla, sin llamar a soporte.

**Criterios de aceptación:**

- Una lista de los servicios (CRM y calendario, voz, línea telefónica, análisis,
  servidor) con un indicador verde o rojo cada uno.
- Nombres en lenguaje del negocio, no "GoHighLevel" o "Anthropic".
- Si algo está caído, muestra el detalle del error y avisa que por eso pueden faltar
  datos en el panel.

**Por qué importa:** convierte "no entiendo por qué está en cero" en "ah, la línea
está caída" sin una llamada de soporte. Cada servicio se revisa por separado: uno
caído no tumba el reporte de los otros cuatro.

---

## Historia transversal — la que ata todas

> **Como** dueña que no es técnica,
> **quiero** que cuando el panel no pueda obtener un dato, **me diga que no pudo** en
> vez de mostrarme un cero,
> **para** no creer que Sofía no trabajó cuando en realidad fue el panel el que no
> pudo leer.

**Criterio de aceptación:** en ninguna sección, bajo ninguna falla, aparece un `0` o
una tabla vacía cuando la causa real es que la fuente no respondió. Siempre "Dato no
disponible" con el motivo. Este criterio aplica a las siete secciones y es el que más
peso tiene: un cero falso es la única forma en que este panel puede mentirle a su
dueño.
