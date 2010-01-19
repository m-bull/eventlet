from eventlet import hubs
from eventlet.support import greenlets as greenlet

__all__ = ['Event']

class NOT_USED:
    def __repr__(self):
        return 'NOT_USED'

NOT_USED = NOT_USED()
                
class Event(object):
    """An abstraction where an arbitrary number of coroutines
    can wait for one event from another.

    Events are similar to a Queue that can only hold one item, but differ
    in two important ways:

    1. calling :meth:`send` never unschedules the current greenthread
    2. :meth:`send` can only be called once; use :meth:`reset` to prepare the
       event for another :meth:`send`
    
    They are good for communicating results between coroutines.

    >>> from eventlet import event
    >>> import eventlet
    >>> evt = event.Event()
    >>> def baz(b):
    ...     evt.send(b + 1)
    ...
    >>> _ = eventlet.spawn_n(baz, 3)
    >>> evt.wait()
    4
    """
    _result = None
    def __init__(self):
        self._waiters = set()
        self.reset()

    def __str__(self):
        params = (self.__class__.__name__, hex(id(self)), self._result, self._exc, len(self._waiters))
        return '<%s at %s result=%r _exc=%r _waiters[%d]>' % params

    def reset(self):
        """ Reset this event so it can be used to send again.
        Can only be called after :meth:`send` has been called.

        >>> from eventlet import event
        >>> evt = event.Event()
        >>> evt.send(1)
        >>> evt.reset()
        >>> evt.send(2)
        >>> evt.wait()
        2

        Calling reset multiple times in a row is an error.

        >>> evt.reset()
        >>> evt.reset()
        Traceback (most recent call last):
        ...
        AssertionError: Trying to re-reset() a fresh event.

        """
        assert self._result is not NOT_USED, 'Trying to re-reset() a fresh event.'
        self._result = NOT_USED
        self._exc = None

    def ready(self):
        """ Return true if the :meth:`wait` call will return immediately.
        Used to avoid waiting for things that might take a while to time out.
        For example, you can put a bunch of events into a list, and then visit
        them all repeatedly, calling :meth:`ready` until one returns ``True``,
        and then you can :meth:`wait` on that one."""
        return self._result is not NOT_USED

    def has_exception(self):
        return self._exc is not None

    def has_result(self):
        return self._result is not NOT_USED and self._exc is None

    def poll(self, notready=None):
        if self.ready():
            return self.wait()
        return notready

    # QQQ make it return tuple (type, value, tb) instead of raising
    # because
    # 1) "poll" does not imply raising
    # 2) it's better not to screw up caller's sys.exc_info() by default
    #    (e.g. if caller wants to calls the function in except or finally)
    def poll_exception(self, notready=None):
        if self.has_exception():
            return self.wait()
        return notready

    def poll_result(self, notready=None):
        if self.has_result():
            return self.wait()
        return notready

    def wait(self):
        """Wait until another coroutine calls :meth:`send`.
        Returns the value the other coroutine passed to
        :meth:`send`.

        >>> from eventlet import event
        >>> import eventlet
        >>> evt = event.Event()
        >>> def wait_on():
        ...    retval = evt.wait()
        ...    print "waited for", retval
        >>> _ = eventlet.spawn(wait_on)
        >>> evt.send('result')
        >>> eventlet.sleep(0)
        waited for result

        Returns immediately if the event has already
        occured.

        >>> evt.wait()
        'result'
        """
        current = greenlet.getcurrent()
        if self._result is NOT_USED:
            self._waiters.add(current)
            try:
                return hubs.get_hub().switch()
            finally:
                self._waiters.discard(current)
        if self._exc is not None:
            current.throw(*self._exc)
        return self._result

    def send(self, result=None, exc=None):
        """Makes arrangements for the waiters to be woken with the
        result and then returns immediately to the parent.

        >>> from eventlet import event
        >>> import eventlet
        >>> evt = event.Event()
        >>> def waiter():
        ...     print 'about to wait'
        ...     result = evt.wait()
        ...     print 'waited for', result
        >>> _ = eventlet.spawn(waiter)
        >>> eventlet.sleep(0)
        about to wait
        >>> evt.send('a')
        >>> eventlet.sleep(0)
        waited for a

        It is an error to call :meth:`send` multiple times on the same event.

        >>> evt.send('whoops')
        Traceback (most recent call last):
        ...
        AssertionError: Trying to re-send() an already-triggered event.

        Use :meth:`reset` between :meth:`send` s to reuse an event object.
        """
        assert self._result is NOT_USED, 'Trying to re-send() an already-triggered event.'
        self._result = result
        if exc is not None and not isinstance(exc, tuple):
            exc = (exc, )
        self._exc = exc
        hub = hubs.get_hub()
        if self._waiters:
            hub.schedule_call_global(0, self._do_send, self._result, self._exc, self._waiters.copy())

    def _do_send(self, result, exc, waiters):
        while waiters:
            waiter = waiters.pop()
            if waiter in self._waiters:
                if exc is None:
                    waiter.switch(result)
                else:
                    waiter.throw(*exc)

    def send_exception(self, *args):
        """Same as :meth:`send`, but sends an exception to waiters."""
        # the arguments and the same as for greenlet.throw
        return self.send(None, args)                