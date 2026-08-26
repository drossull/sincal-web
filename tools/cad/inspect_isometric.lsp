;;; Diagnóstico de solo lectura: encuentra el único título VISTA ISOMETRICA.
;;; El archivo de reporte se recibe como argumento de sincal:inspect.
(vl-load-com)

(defun sincal:write (stream value)
  (write-line value stream)
)

;;; La exportación corre sobre una copia de sólo lectura: habilitar capas evita
;;; que SSGET omita la geometría técnica bloqueada por el plano de origen.
(defun sincal:unlock-all-layers (/ document layers layer)
  (command "_.-layer" "_thaw" "*" "_on" "*" "_unlock" "*" "")
)

;;; SSGET con ventana omite entidades no seleccionables del plano. Este filtro
;;; consulta la base completa y conserva las entidades cuyo punto de inserción
;;; o inicio está dentro del marco WCS.
(defun sincal:selection-by-point (lower upper / all output index entity data point worldPoint)
  (setq all (ssget "_X"))
  (setq output (ssadd))
  (if all
    (progn
      (setq index 0)
      (while (< index (sslength all))
        (setq entity (ssname all index))
        (setq data (entget entity))
        (setq point (cdr (assoc 10 data)))
        (if point
          (progn
            (setq worldPoint (trans point entity 0))
            (if (and (>= (car worldPoint) (car lower))
                     (<= (car worldPoint) (car upper))
                     (>= (cadr worldPoint) (cadr lower))
                     (<= (cadr worldPoint) (cadr upper)))
              (ssadd entity output)
            )
          )
        )
        (setq index (1+ index))
      )
    )
  )
  output
)

(defun sincal:clean (value)
  (vl-string-translate "|" " " value)
)

(defun sincal:text-value (data / value pair)
  (setq value "")
  (foreach pair data
    (if (member (car pair) '(1 3))
      (setq value (strcat value (cdr pair)))
    )
  )
  value
)

(defun sincal:is-title (value)
  (not (null (vl-string-search "VISTA ISOMETRICA" (strcase (sincal:clean value)))))
)

(defun sincal:point-string (point)
  (if point
    (strcat (rtos (car point) 2 3) "," (rtos (cadr point) 2 3) "," (rtos (caddr point) 2 3))
    ""
  )
)

(defun sincal:layout (data)
  (if (assoc 410 data) (cdr (assoc 410 data)) "Model")
)

(defun sincal:report (stream entityType tag value block handle layer layout insertion)
  (sincal:write stream (strcat "MATCH|" entityType "|" tag "|" (sincal:clean value) "|" block "|" handle "|" layer "|" layout "|" insertion))
)

(defun sincal:inspect (reportPath / stream selection index entity data entityType value nextEntity attribute blockName)
  (setq stream (open reportPath "w"))
  (setq selection (ssget "_X" '((0 . "TEXT,MTEXT,INSERT"))))
  (if selection
    (progn
      (setq index 0)
      (while (< index (sslength selection))
        (setq entity (ssname selection index))
        (setq data (entget entity))
        (setq entityType (cdr (assoc 0 data)))
        (cond
          ((member entityType '("TEXT" "MTEXT"))
            (setq value (sincal:text-value data))
            (if (sincal:is-title value)
              (sincal:report stream entityType "" value "" (cdr (assoc 5 data)) (cdr (assoc 8 data)) (sincal:layout data) (sincal:point-string (cdr (assoc 10 data))))
            )
          )
          ((= entityType "INSERT")
            (setq blockName (cdr (assoc 2 data)))
            (setq nextEntity (entnext entity))
            (while (and nextEntity (/= "SEQEND" (cdr (assoc 0 (entget nextEntity)))))
              (setq attribute (entget nextEntity))
              (if (= "ATTRIB" (cdr (assoc 0 attribute)))
                (progn
                  (setq value (sincal:text-value attribute))
                  (if (sincal:is-title value)
                    (sincal:report stream "ATTRIB" (cdr (assoc 2 attribute)) value blockName (cdr (assoc 5 data)) (cdr (assoc 8 data)) (sincal:layout data) (sincal:point-string (cdr (assoc 10 attribute))))
                  )
                )
              )
              (setq nextEntity (entnext nextEntity))
            )
          )
        )
        (setq index (1+ index))
      )
    )
  )
  (sincal:write stream "DONE")
  (close stream)
  (princ)
)

;;; Vista temporal para calibrar el marco de la isométrica antes de recortarla.
;;; El PNG es sólo diagnóstico; la entrega final se exportará a DXF/SVG.
(defun sincal:preview-isometric (outputPath x y / lower upper)
  (setq lower (list (- x 2500.0) (- y 400.0) 0.0))
  (setq upper (list (+ x 2500.0) (+ y 3000.0) 0.0))
  (command "_.zoom" "_w" lower upper)
  (command "_.pngout" outputPath)
  (princ)
)

;;; Exporta el marco de la isométrica como SVG vectorial para la web.
;;; El mismo marco se usará después para producir el DXF aislado.
(defun sincal:export-isometric-svg (outputPath x y / lower upper selection)
  (setvar "CTAB" "Model")
  (command "_.ucs" "_world")
  (sincal:unlock-all-layers)
  (setq lower (list (- x 100.0) (- y 20.0) 0.0))
  (setq upper (list (+ x 100.0) (+ y 150.0) 0.0))
  (setq selection (sincal:selection-by-point lower upper))
  (if selection
    (command "_.svgout" selection "" outputPath)
  )
  (princ)
)

;;; Crea un DWG temporal con la selección que enmarca la vista isométrica.
;;; Se convierte a DXF en una segunda apertura de Core Console.
(defun sincal:export-isometric-dwg (outputPath x y / lower upper selection)
  (setvar "CTAB" "Model")
  (command "_.ucs" "_world")
  (sincal:unlock-all-layers)
  (setq lower (list (- x 100.0) (- y 20.0) 0.0))
  (setq upper (list (+ x 100.0) (+ y 150.0) 0.0))
  (setq selection (sincal:selection-by-point lower upper))
  (if selection
    (progn
      (sssetfirst nil selection)
      (command "_.-wblock" output)
    )
  )
  (princ)
)

;;; Exporta directamente a DXF el conjunto seleccionado en el plano fuente.
(defun sincal:export-isometric-dxf (outputPath x y / lower upper selection)
  (setvar "CTAB" "Model")
  (command "_.ucs" "_world")
  (sincal:unlock-all-layers)
  (setq lower (list (- x 100.0) (- y 20.0) 0.0))
  (setq upper (list (+ x 100.0) (+ y 150.0) 0.0))
  (setq selection (sincal:selection-by-point lower upper))
  (if selection
    (command "_.dxfout" output selection "")
  )
  (princ)
)

;;; Reporta las entidades del marco durante la calibración del recorte.
(defun sincal:inspect-isometric-crop (reportPath x y / lower upper selection index entity data)
  (setvar "CTAB" "Model")
  (command "_.ucs" "_world")
  (sincal:unlock-all-layers)
  (setq lower (list (- x 100.0) (- y 20.0) 0.0))
  (setq upper (list (+ x 100.0) (+ y 150.0) 0.0))
  (setq selection (sincal:selection-by-point lower upper))
  (setq stream (open reportPath "w"))
  (if selection
    (progn
      (setq index 0)
      (while (< index (sslength selection))
        (setq entity (ssname selection index))
        (setq data (entget entity))
        (sincal:write stream (strcat (cdr (assoc 0 data)) "|" (cdr (assoc 5 data)) "|" (cdr (assoc 8 data)) "|" (if (assoc 2 data) (cdr (assoc 2 data)) "")))
        (setq index (1+ index))
      )
    )
  )
  (sincal:write stream "DONE")
  (close stream)
  (princ)
)

;;; Enumera referencias externas insertadas para resolver vistas alojadas en XREF.
(defun sincal:inspect-xrefs (reportPath x y / stream selection index entity data block dataBlock)
  (setq stream (open reportPath "w"))
  (setq selection (ssget "_X" '((0 . "INSERT"))))
  (if selection
    (progn
      (setq index 0)
      (while (< index (sslength selection))
        (setq entity (ssname selection index))
        (setq data (entget entity))
        (setq block (cdr (assoc 2 data)))
        (setq dataBlock (tblsearch "BLOCK" block))
        (if (and dataBlock (= 4 (logand 4 (cdr (assoc 70 dataBlock)))))
          (sincal:write stream (strcat block "|" (sincal:point-string (cdr (assoc 10 data))) "|" (if (assoc 1 dataBlock) (cdr (assoc 1 dataBlock)) "")))
        )
        (setq index (1+ index))
      )
    )
  )
  (sincal:write stream "DONE")
  (close stream)
  (princ)
)

;;; Obtiene la inserción WCS del título, necesaria cuando su OCS no es mundo.
(defun sincal:inspect-title-coordinate (reportPath x y / stream selection index entity data point)
  (setq stream (open reportPath "w"))
  (setq selection (ssget "_X" '((0 . "TEXT,MTEXT"))))
  (if selection
    (progn
      (setq index 0)
      (while (< index (sslength selection))
        (setq entity (ssname selection index))
        (setq data (entget entity))
        (if (sincal:is-title (sincal:text-value data))
          (progn
            (setq point (cdr (assoc 10 data)))
            (sincal:write stream (strcat "RAW|" (sincal:point-string point)))
            (sincal:write stream (strcat "WCS|" (sincal:point-string (trans point entity 0))))
          )
        )
        (setq index (1+ index))
      )
    )
  )
  (sincal:write stream "DONE")
  (close stream)
  (princ)
)

;;; Muestra los puntos de referencia CAD más cercanos al título para calibrar
;;; el marco antes de exportar una isométrica de un plano con capas mixtas.
(defun sincal:inspect-near-title (reportPath x y / stream selection index entity data point worldPoint records record sorted count)
  (setq selection (ssget "_X"))
  (setq records '())
  (if selection
    (progn
      (setq index 0)
      (while (< index (sslength selection))
        (setq entity (ssname selection index))
        (setq data (entget entity))
        (setq point (cdr (assoc 10 data)))
        (if point
          (progn
            (setq worldPoint (trans point entity 0))
            (if (and (< (abs (- (car worldPoint) x)) 100000.0)
                     (< (abs (- (cadr worldPoint) y)) 100000.0))
              (setq records (cons (list (distance worldPoint (list x y 0.0))
                                        (cdr (assoc 0 data))
                                        (cdr (assoc 8 data))
                                        worldPoint)
                                  records))
            )
          )
        )
        (setq index (1+ index))
      )
    )
  )
  (setq sorted (vl-sort records '(lambda (left right) (< (car left) (car right)))))
  (setq stream (open reportPath "w"))
  (setq count 0)
  (foreach record sorted
    (if (< count 80)
      (progn
        (sincal:write stream (strcat (rtos (car record) 2 3) "|" (cadr record) "|" (caddr record) "|" (sincal:point-string (cadddr record))))
        (setq count (1+ count))
      )
    )
  )
  (sincal:write stream "DONE")
  (close stream)
  (princ)
)

;;; Lista etiquetas alrededor y por encima del rótulo para ubicar el marco de la vista.
(defun sincal:inspect-isometric-labels (reportPath x y / stream selection index entity data point worldPoint type)
  (setq stream (open reportPath "w"))
  (setq selection (ssget "_X" '((0 . "TEXT,MTEXT"))))
  (if selection
    (progn
      (setq index 0)
      (while (< index (sslength selection))
        (setq entity (ssname selection index))
        (setq data (entget entity))
        (setq point (cdr (assoc 10 data)))
        (setq worldPoint (trans point entity 0))
        (if (and (> (car worldPoint) (- x 15000.0))
                 (< (car worldPoint) (+ x 15000.0))
                 (> (cadr worldPoint) (- y 3000.0))
                 (< (cadr worldPoint) (+ y 20000.0)))
          (sincal:write stream (strcat (cdr (assoc 0 data)) "|" (sincal:clean (sincal:text-value data)) "|" (cdr (assoc 8 data)) "|" (sincal:point-string worldPoint)))
        )
        (setq index (1+ index))
      )
    )
  )
  (sincal:write stream "DONE")
  (close stream)
  (princ)
)
