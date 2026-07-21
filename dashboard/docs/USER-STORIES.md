# USER STORIES — El dashboard de Sofía

> Las siete secciones del panel, contadas desde la silla del **dueño de la
> clínica**. No es quien construyó el sistema: es quien lo abre en la mañana con
> un café para saber si valió la pena pagarlo. Cada historia trae su criterio de
> aceptación —cómo sabemos que quedó bien— y el porqué detrás.

El personaje: **el Dr. o la dueña de "Clínica Dental Sonrisa Perfecta".** No es
técnico. No quiere entrar a Retell, ni a GoHighLevel, ni a leer un log. Quiere una
página que le diga, de un vistazo, si Sofía trabajó.

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
> **para** revisar una llamada puntual sin escuchar el audio ni entrar a Retell.

**Criterios de aceptación:**

- Tabla con: paciente, fecha, origen, duración, resultado (agendó / no agendó),
  urgencia, resumen.
- Un renglón se abre y muestra el resumen completo, los scores (interés,
  probabilidad de asistir), qué hizo el sistema (qué tools se dispararon) y la
  transcripción entera.
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

## 6. Llamar a un paciente

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

## 7. Estado del sistema

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
