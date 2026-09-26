; What is worth folding away in PL4G.
;
; A definition folds as a whole, attributes and signature and body together,
; because what a reader wants left on the screen is the line that says what this
; is.  A block folds as well, so that the arms of an `if` or the body of a loop
; can be put away without putting away the function they are in.
;
; A block begins where the line that opens it ends -- just after the `:` -- so
; folding a function leaves its signature and folding its body leaves the
; signature and the attributes: two folds, one inside the other, and neither of
; them the same lines as the other.
;
; Nothing here turns folding on.  How a file is folded is the reader's business
; and an editor that decided it would be deciding for every file; `editors/nvim`
; says which two settings ask for this.
[
  (function_definition)
  (type_definition)
  (enum_definition)
  (layout_block)
  (explicit_block)
] @fold
