(defpackage :gtirb/gtirb
  (:nicknames :gtirb)
  (:use :common-lisp :alexandria :graph)
  (:import-from :uiop :nest)
  (:import-from :bit-smasher :octets->int :int->octets)
  (:shadow :symbol :block)
  (:export :read-gtirb-proto :write-gtirb-proto))
(in-package :gtirb/gtirb)

(defun read-gtirb-proto (path)
  "Read GTIRB from PATH."
  (assert (probe-file path) (path)
          "Can't read GTIRB from ~s, because the file doesn't exist."
          path)
  (let ((gtirb (make-instance 'proto:ir)))
    (with-open-file (input path
                           :direction :input :element-type 'unsigned-byte)
      (let* ((size (file-length input))
             (buffer (make-array size :element-type '(unsigned-byte 8))))
        (read-sequence buffer input)
        (pb:merge-from-array gtirb buffer 0 size)))
    gtirb))

(defun write-gtirb-proto (gtirb path)
  "Write GTIRB to PATH."
  (let* ((size (pb:octet-size gtirb))
         (buffer (make-array size :element-type '(unsigned-byte 8))))
    (pb:serialize gtirb buffer 0 size)
    (with-open-file (output path
                            :direction :output :if-exists :supersede
                            :element-type 'unsigned-byte)
      (write-sequence buffer output)))
  (values))


;;;; Classes
(defclass module ()
  ((proto :initarg :proto :accessor proto :type proto:module
          :documentation "Backing protobuf object.")
   (cfg :accessor cfg :type cfg
        :documentation "Module control flow block (CFG).")
   (blocks :accessor blocks :type hash-table
           :documentation "Module control flow block (CFG).")
   (aux-data :accessor aux-data :type (list aux-data)
             :documentation "Module auxiliary data objects.")))

(defmethod name ((obj module))
  (pb:string-value (proto:name (proto obj))))

(defmethod (setf name) ((obj module) new)
  (setf (proto:name (proto obj)) (pb:string-field new)))

(define-constant +module-isa-map+
    '((#.proto:+isaid-isa-undefined+ . :undefined)
      (#.proto:+isaid-ia32+ . :ia32)
      (#.proto:+isaid-ppc32+ . :ppc32)
      (#.proto:+isaid-x64+ . :x64)
      (#.proto:+isaid-arm+ . :arm)
      (#.proto:+isaid-valid-but-unsupported+ . :valid-but-unsupported))
  :test #'equal)

(defmethod isa ((obj module))
  (cdr (assoc (proto:isa-id (proto obj)) +module-isa-map+)))

(defmethod (setf isa) (new (obj module))
  (setf (proto:isa-id (proto obj))
        (car (rassoc new +module-isa-map+))))

(define-constant +module-file-format-map+
    '((#.proto:+file-format-coff+ . :coff)
      (#.proto:+file-format-elf+ . :elf)
      (#.proto:+file-format-ida-pro-db32+ . :ida-pro-db32)
      (#.proto:+file-format-ida-pro-db64+ . :ida-pro-db64)
      (#.proto:+file-format-macho+ . :macho)
      (#.proto:+file-format-pe+ . :pe)
      (#.proto:+file-format-raw+ . :raw)
      (#.proto:+file-format-xcoff+ . :xcoff)
      (#.proto:+file-format-format-undefined+ . :format-undefined))
  :test #'equal)

(defmethod file-format ((obj module))
  (cdr (assoc (proto:file-format (proto obj)) +module-file-format-map+)))

(defmethod (setf file-format) (new (obj module))
  (setf (proto:file-format (proto obj))
        (car (rassoc new +module-file-format-map+))))

(defmethod initialize-instance :after ((obj module) &key)
  ;; Repackage the AuxData into an alist keyed by name.
  (let ((p-aux-data (proto:aux-data (proto:aux-data-container (proto obj))))
        (aux-data '()))
    (dotimes (n (length p-aux-data))
      (push (cons (pb:string-value (proto:key (aref p-aux-data n)))
                  (make-instance 'aux-data
                    :proto (proto:value (aref p-aux-data n))))
            aux-data))
    (setf (aux-data obj) aux-data))
  ;; Package the blocks into a has keyed by UUID.
  (let ((p-blocks (proto:blocks (proto obj)))
        (block-h (make-hash-table)))
    (dotimes (n (length p-blocks))
      (let ((p-block (aref p-blocks n)))
        (setf (gethash (uuid-to-integer (proto:uuid p-block))
                       block-h)
              p-block)))
    (setf (blocks obj) block-h))
  ;; Build the CFG as a lisp graph.
  (nest
   (with-slots (cfg) obj)
   (let ((p-cfg (proto:cfg (proto obj)))))
   (setf cfg)
   (populate
    (make-instance 'digraph)
    :edges-w-values
    (mapcar (lambda (edge)
              (list (list (uuid-to-integer (proto:source-uuid edge))
                          (uuid-to-integer (proto:target-uuid edge)))
                    (make-instance 'edge-label :proto (proto:label edge))))
            (coerce (proto:edges p-cfg) 'list))
    :nodes (map 'list  #'uuid-to-integer (proto:vertices p-cfg)))))

(defmethod get-block ((uuid simple-array) (obj module))
  (get-block (uuid-to-integer uuid) obj))

(defmethod (setf get-block) (new (uuid simple-array) (obj module))
  (setf (get-block (uuid-to-integer uuid) obj) new))

(defmethod get-block ((uuid integer) (obj module))
  (gethash uuid (blocks obj)))

(defmethod (setf get-block) (new (uuid integer) (obj module))
  (setf (gethash uuid (blocks obj)) new))

(defun uuid-to-integer (uuid)
  (octets->int
   (make-array 16 :element-type '(unsigned-byte 8) :initial-contents uuid)))

(defun integer-to-uuid (number)
  (int->octets number))

(defmethod print-object ((obj module) (stream stream))
  (print-unreadable-object (obj stream :type t :identity cl:t)
    (format stream "~a ~a ~s" (file-format obj) (isa obj) (name obj))))

(defclass edge-label ()
  ((proto :initarg :proto :accessor proto :type proto:module
          :documentation "Backing protobuf object.")))

(define-constant +edge-label-type-map+
    '((#.proto:+edge-type-type-branch+ . :branch)
      (#.proto:+edge-type-type-call+ . :call)
      (#.proto:+edge-type-type-fallthrough+ . :fallthrough)
      (#.proto:+edge-type-type-return+ . :return)
      (#.proto:+edge-type-type-syscall+ . :syscall)
      (#.proto:+edge-type-type-sysret+ . :sysret))
  :test #'equal)

(defmethod edge-type ((obj edge-label))
  (cdr (assoc (proto:type (proto obj)) +edge-label-type-map+)))

(defmethod (setf edge-type) (new (obj edge-label))
  (setf (proto:type (proto obj))
        (car (rassoc new +edge-label-type-map+))))

(defmethod conditional ((obj edge-label))
  (proto:conditional (proto obj)))

(defmethod (setf conditional) (new (obj edge-label))
  (setf (proto:conditional (proto obj)) new))

(defmethod direct ((obj edge-label))
  (proto:direct (proto obj)))

(defmethod (setf direct) (new (obj edge-label))
  (setf (proto:direct (proto obj)) new))

(defmethod print-object ((obj edge-label) (stream stream))
  (print-unreadable-object (obj stream :type t :identity cl:t)
    (format stream "~a ~a ~a"
            (edge-type obj)
            (if (conditional obj) :conditional :unconditional)
            (if (direct obj) :direct :undirect))))

(defclass aux-data ()
  ((proto :initarg :proto :accessor proto :type proto:module
          :documentation "Backing protobuf object.")))

(defmethod aux-data-type ((obj aux-data))
  (pb:string-value (proto:type-name (proto obj))))

(defmethod (setf aux-data-type) ((new string) (obj aux-data))
  (setf (proto:type-name (proto obj)) (pb:string-field new)))

(defmethod data ((obj aux-data))
  ;; TODO: Implement the parsing and reading/writing of data by type.
  (warn "Not implemented for ~a." obj))

(defclass gtirb ()
  ((proto :initarg :proto :accessor proto :type proto:module
          :documentation "Backing protobuf object.")
   (modules :initarg modules :accessor modules :initform nil :type (list module)
            :documentation "List of the modules on an IR.")))

(defmethod (setf modules) :after (new (obj gtirb))
  (setf (proto:modules (proto obj))
        (coerce (mapcar #'proto (modules obj)) 'vector)))

(defmethod initialize-instance :after ((obj gtirb) &key)
  (with-slots (modules) obj
    (setf modules (mapcar (lambda (module-proto)
                            (make-instance 'module :proto module-proto))
                          (coerce (proto:modules (proto obj)) 'list)))))

(defmethod print-object ((obj gtirb) (stream stream))
  (print-unreadable-object (obj stream :type t :identity cl:t)
    (format stream "~a" (modules obj))))

