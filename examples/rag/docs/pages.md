# Pages and views

A page is a view served at an address: `page['/', home]` renders the view for each visit and keeps it live over a socket, so a change to a fact renders again without a reload.

A view is a function that answers elements: a stack of headings, paragraphs, fields and buttons. A field is bound to a fact, and a button names the handler its click runs.

A handler is a transaction. It runs on a working copy of the session's facts, sees its own writes at once, and when it ends its writes land as one. A render never sees a handler half done.

A page and its session: the page request renders the reader's session once, and the page's socket adopts that session, its facts and the render already on screen.
