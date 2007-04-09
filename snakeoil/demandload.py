# Copyright: 2006 Vadim Gelfer <vadim.gelfer@gmail.com>
# Copyright: 2007 Marien Zwart <marienz@gentoo.org>
# License: GPL2

"""Demand load things when used.

This uses L{Placeholder} objects which create an actual object on
first use and know how to replace themselves with that object, so
there is no performance penalty after first use.

This trick is *mostly* transparent, but there are a few things you
have to be careful with:

 - You may not bind a second name to a placeholder object. Specifically,
   if you demandload C{bar} in module C{foo}, you may not
   C{from foo import bar} in a third module. The placeholder object
   does not "know" it gets imported, so this does not trigger the
   demandload: C{bar} in the third module is the placeholder object.
   When that placeholder gets used it replaces itself with the actual
   module in C{foo} but not in the third module.
   Because this is normally unwanted (it introduces a small
   performance hit) the placeholder object will raise an exception if
   it detects this. But if the demandload gets triggered before the
   third module is imported you do not get that exception, so you
   have to be careful not to import or otherwise pass around the
   placeholder object without triggering it.
 - Not all operations on the placeholder object trigger demandload.
   The most common problem is that C{except ExceptionClass} does not
   work if C{ExceptionClass} is a placeholder.
   C{except module.ExceptionClass} with C{module} a placeholder does
   work. You can normally avoid this by always demandloading the
   module, not something in it.
"""

# TODO: the use of a curried func instead of subclassing needs more thought.

# the replace_func used by Placeholder is currently passed in as an
# external callable, with "partial" used to provide arguments to it.
# This works, but has the disadvantage that calling
# demand_compile_regexp needs to import re (to hand re.compile to
# partial). One way to avoid that would be to add a wrapper function
# that delays the import (well, triggers the demandload) at the time
# the regexp is used, but that's a bit convoluted. A different way is
# to make replace_func a method of Placeholder implemented through
# subclassing instead of a callable passed to its __init__. The
# current version does not do this because getting/setting attributes
# of Placeholder is annoying because of the
# __getattribute__/__setattr__ override.


from snakeoil.modules import load_any
from snakeoil.currying import partial

# There are some demandloaded imports below the definition of demandload.

_allowed_chars = "".join((x.isalnum() or x in "_.") and " " or "a" 
    for x in map(chr, xrange(256)))

def parse_imports(imports):
    """Parse a sequence of strings describing imports.

    For every input string it returns a tuple of (import, targetname).
    Examples::

      'foo' -> ('foo', 'foo')
      'foo:bar' -> ('foo.bar', 'bar')
      'foo:bar,baz@spork' -> ('foo.bar', 'bar'), ('foo.baz', 'spork')
      'foo@bar' -> ('foo', 'bar')

    Notice 'foo.bar' is not a valid input. This simplifies the code,
    but if it is desired it can be added back.

    @type  imports: sequence of C{str} objects.
    @rtype: iterable of tuples of two C{str} objects.
    """
    for s in imports:
        fromlist = s.split(':', 1)
        if len(fromlist) == 1:
            # Not a "from" import.
            if '.' in s:
                raise ValueError('dotted imports unsupported.')
            split = s.split('@', 1)
            for s in split:
                if not s.translate(_allowed_chars).isspace():
                    raise ValueError("bad target: %s" % s)
            if len(split) == 2:
                yield tuple(split)
            else:
                yield split[0], split[0]
        else:
            # "from" import.
            base, targets = fromlist
            if not base.translate(_allowed_chars).isspace():
                raise ValueError("bad target: %s" % base)
            for target in targets.split(','):
                split = target.split('@', 1)
                for s in split:
                    if not s.translate(_allowed_chars).isspace():
                        raise ValueError("bad target: %s" % s)
                yield base + '.' + split[0], split[-1]


class Placeholder(object):

    """Object that knows how to replace itself when first accessed.

    See the module docstring for common problems with its use.
    """

    def __init__(self, scope, name, replace_func):
        """Initialize.

        @param scope: the scope we live in, normally the result of
          C{globals()}.
        @param name: the name we have in C{scope}.
        @param replace_func: callable returning the object to replace us with.
        """
        object.__setattr__(self, '_scope', scope)
        object.__setattr__(self, '_name', name)
        object.__setattr__(self, '_replace_func', replace_func)

    def _already_replaced(self):
        name = object.__getattribute__(self, '_name')
        raise ValueError('Placeholder for %r was triggered twice' % (name,))

    def _replace(self):
        """Replace ourself in C{scope} with the result of our C{replace_func}.

        @returns: the result of calling C{replace_func}.
        """
        replace_func = object.__getattribute__(self, '_replace_func')
        scope = object.__getattribute__(self, '_scope')
        name = object.__getattribute__(self, '_name')
        # Paranoia, explained in the module docstring.
        already_replaced = object.__getattribute__(self, '_already_replaced')
        object.__setattr__(self, '_replace_func', already_replaced)

        # Cleanup, possibly unnecessary.
        object.__setattr__(self, '_scope', None)

        result = replace_func()
        scope[name] = result
        return result

    # Various methods proxied to our replacement.

    def __getattribute__(self, attr):
        result = object.__getattribute__(self, '_replace')()
        return getattr(result, attr)

    def __setattr__(self, attr, value):
        result = object.__getattribute__(self, '_replace')()
        setattr(result, attr, value)

    def __call__(self, *args, **kwargs):
        result = object.__getattribute__(self, '_replace')()
        return result(*args, **kwargs)


def demandload(scope, *imports):
    """Import modules into scope when each is first used.

    scope should be the value of C{globals()} in the module calling
    this function. (using C{locals()} may work but is not recommended
    since mutating that is not safe).

    Other args are strings listing module names.
    names are handled like this::

      foo            import foo
      foo@bar        import foo as bar
      foo:bar        from foo import bar
      foo:bar,quux   from foo import bar, quux
      foo.bar:quux   from foo.bar import quux
      foo:baz@quux   from foo import baz as quux
    """
    for source, target in parse_imports(imports):
        scope[target] = Placeholder(scope, target, partial(load_any, source))


demandload(globals(), 'warnings', 're')

# Extra name to make undoing monkeypatching demandload with
# disabled_demandload easier.
enabled_demandload = demandload


def disabled_demandload(scope, *imports):
    """Exactly like L{demandload} but does all imports immediately."""
    for source, target in parse_imports(imports):
        scope[target] = load_any(source)


class RegexPlaceholder(Placeholder):
    """
    Compiled Regex object that knows how to replace itself when first accessed.

    See the module docstring for common problems with its use; used by
    L{demand_compile_regexp}.
    """

    def _replace(self):
        args, kwargs = object.__getattribute__(self, '_replace_func')
        object.__setattr__(self, '_replace_func',
            partial(re.compile, *args, **kwargs))
        return Placeholder._replace(self)



def demand_compile_regexp(scope, name, *args, **kwargs):
    """Demandloaded version of L{re.compile}.

    Extra arguments are passed unchanged to L{re.compile}.

    This returns the placeholder, which you *must* bind to C{name} in
    the scope you pass as C{scope}. It is done this way to prevent
    confusing code analysis tools like pylint.

    @param scope: the scope, just like for L{demandload}.
    @param name: the name of the compiled re object in that scope.
    @returns: the placeholder object.
    """
    return RegexPlaceholder(scope, name, (args, kwargs))
