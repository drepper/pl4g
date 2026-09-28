; Where the ordinary rules do not hold: inside a comment and inside a string.
;
; What reads this is the bracket behaviour above -- a quote typed inside a string
; is a quote and not the start of another one -- and anything else that asks what
; kind of text the cursor is in.

[(line_comment) (doc_comment)] @comment.inclusive
(string_literal) @string
