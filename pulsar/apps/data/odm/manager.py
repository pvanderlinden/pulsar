from pulsar import Event, wait_complete

from .query import AbstractQuery, Query, QueryError


RECURSIVE_RELATIONSHIP_CONSTANT = 'self'

pending_lookups = {}

class_prepared = Event()


def do_pending_lookups(model, **kwargs):
    """Handle any pending relations to the sending model.
Sent from class_prepared."""
    key = (model._meta.app_label, model._meta.name)
    for callback in pending_lookups.pop(key, []):
        callback(model)


class_prepared.bind(do_pending_lookups)


class OdmError(RuntimeError):
    pass


class ManyToManyError(OdmError):
    pass


class Manager(AbstractQuery):
    '''Used by the :class:`.Mapper` to link a data :class:`.Store` collection
    with a :class:`.Model`.

    For example::

        from pulsar.apps.data import odm

        class MyModel(odm.Model):
            group = odm.SymbolField()
            flag = odm.BooleanField()

        models = odm.Mapper()
        models.register(MyModel)

        manager = models[MyModel]

    A :class:`.Model` can specify a :ref:`custom manager <custom-manager>` by
    creating a :class:`Manager` subclass with additional methods::

        class MyModelManager(odm.Manager):

            def special_query(self, **kwargs):
                ...

    At this point we need to tell the model about the custom manager, and we do
    so by setting the ``manager_class`` class attribute in the
    :class:`.Model`::

        class MyModel(odm.Model):
            ...

            manager_class = MyModelManager

    .. attribute:: _model

        The :class:`.Model` associated with this manager

    .. attribute:: _store

        The :class:`.Store` associated with this manager

    .. attribute:: _mapper

        The :class:`.Mapper` where this :class:`.Manager` is registered
    '''
    query_class = Query

    def __init__(self, model, store=None, read_store=None, mapper=None):
        self._model = model
        self._store = store
        self._read_store = read_store or store
        self._mapper = mapper

    @property
    def _meta(self):
        return self._model._meta

    @property
    def _loop(self):
        return self._store._loop

    def __str__(self):
        if self._store:
            return '{0}({1} - {2})'.format(self.__class__.__name__,
                                           self._meta,
                                           self._store)
        else:
            return '{0}({1})'.format(self.__class__.__name__, self._meta)
    __repr__ = __str__

    def __call__(self, *args, **kwargs):
        '''Create a new model without commiting to database.
        '''
        return self._model(*args, **kwargs)

    def create_table(self, remove_existing=False):
        '''Create the table/collection for the :attr:`_model`
        '''
        return self._store.create_table(self._model,
                                        remove_existing=remove_existing)

    def drop_table(self):
        '''Drop the table/collection for the :attr:`_model`
        '''
        return self._store.drop_table(self._model)

    #    QUERY IMPLEMENTATION
    def query(self):
        '''Build a :class:`.Query` object
        '''
        return self.query_class(self)

    def get(self, *args, **kw):
        if len(args) == 1:
            return self._read_store.get_model(self._model, args[0])
        elif args:
            raise QueryError("'get' expected at most 1 argument, %s given" %
                             len(args))
        else:
            qs = self.filter(**kw)
            return self._get(qs)
            raise NotImplementedError

    def filter(self, **kwargs):
        '''Build a :class:`.Query` object with filtering clauses
        '''
        return self.query().filter(**kwargs)

    def exclude(self, **kwargs):
        return self.query().exclude(**kwargs)

    def union(self, *queries):
        return self.query().exclude(*queries)

    def intersect(self, *queries):
        return self.query().intersect(*queries)

    def where(self, *expressions):
        return self.query().where(*expressions)

    def count(self):
        return self.query().count()

    def all(self):
        return self.query().all()

    def begin(self):
        '''Begin a new :class:`.Transaction`.'''
        return self._mapper.begin()

    @wait_complete
    def create(self, *args, **kwargs):
        '''Create a new instance of :attr:`_model` and commit to server.
        '''
        with self._mapper.begin() as t:
            model = t.add(self(*args, **kwargs))
        return t.wait(lambda t: model)
    new = create
    insert = new

    @wait_complete
    def save(self, instance):
        '''Save an existing ``instance`` of :attr:`_model`.

        If the instance already contain the primary key this is considered
        and update, otherwise an insert.
        '''
        with self._mapper.begin() as t:
            t.add(instance)
        return t.wait(lambda t: instance)
    update = save


def load_relmodel(field, callback):
    relmodel = None
    relation = field.relmodel
    if relation == RECURSIVE_RELATIONSHIP_CONSTANT:
        relmodel = field.model
    else:
        try:
            app_label, model_name = relation.lower().split(".")
        except ValueError:
            # If we can't split, assume a model in current app
            app_label = field.model._meta.app_label
            model_name = relation.lower()
        except AttributeError:
            relmodel = relation
    if relmodel:
        callback(relmodel)
    else:
        key = (app_label, model_name)
        if key not in pending_lookups:
            pending_lookups[key] = []
        pending_lookups[key].append(callback)


class LazyProxy(object):
    '''Base class for lazy descriptors.

    .. attribute:: field

        The :class:`Field` which create this descriptor. Either a
        :class:`ForeignKey` or a :class:`StructureField`.
    '''
    def __init__(self, field):
        self.field = field

    def __repr__(self):
        return self.field.name
    __str__ = __repr__

    @property
    def name(self):
        return self.field.name

    def load(self, instance, session):
        '''Load the lazy data for this descriptor.'''
        raise NotImplementedError

    def load_from_manager(self, manager):
        raise NotImplementedError('cannot access %s from manager' % self)

    def __get__(self, instance, instance_type=None):
        if not self.field.class_field:
            if instance is None:
                return self
            return self.load(instance, instance.session)
        else:
            return self


class LazyForeignKey(LazyProxy):
    '''Descriptor for a :class:`ForeignKey` field.'''
    def load(self, instance, session=None, backend=None):
        return instance._load_related_model(self.field)

    def __set__(self, instance, value):
        if instance is None:
            raise AttributeError("%s must be accessed via instance" %
                                 self._field.name)
        field = self.field
        if value is not None and not isinstance(value, field.relmodel):
            raise ValueError(
                'Cannot assign "%r": "%s" must be a "%s" instance.' %
                (value, field, field.relmodel._meta.name))

        cache_name = self.field.get_cache_name()
        # If we're setting the value of a OneToOneField to None,
        # we need to clear
        # out the cache on any old related object. Otherwise, deleting the
        # previously-related object will also cause this object to be deleted,
        # which is wrong.
        if value is None:
            # Look up the previously-related object, which may still
            # be available since we've not yet cleared out the related field.
            related = getattr(instance, cache_name, None)
            if related:
                try:
                    delattr(instance, cache_name)
                except AttributeError:
                    pass
            setattr(instance, self.field.attname, None)
        else:
            setattr(instance, self.field.attname, value.pkvalue())
            setattr(instance, cache_name, value)


class RelatedManager(Manager):
    '''A :class:`.Manager` handling relationships between models.

    .. attribute:: relmodel

        The :class:`.Model` this related manager relates to.

    .. attribute:: related_instance

        An instance of the :attr:`relmodel`.
    '''
    def __init__(self, field, model=None, instance=None):
        self.field = field
        model = model or field.model
        super(RelatedManager, self).__init__(model)
        self.related_instance = instance

    def __get__(self, instance, instance_type=None):
        return self.__class__(self.field, self.model, instance)



class OneToManyRelatedManager(RelatedManager):
    '''A specialised :class:`.RelatedManager` for handling one-to-many
    relationships.

    If a model has a :class:`ForeignKey` field, instances of
    that model will have access to the related (foreign) objects
    via a simple attribute of the model.
    '''
    @property
    def relmodel(self):
        return self.field.relmodel

    def query(self, session=None):
        # Override query method to account for related instance if available
        query = super(OneToManyRelatedManager, self).query(session)
        if self.related_instance is not None:
            kwargs = {self.field.name: self.related_instance}
            return query.filter(**kwargs)
        else:
            return query

    def query_from_query(self, query, params=None):
        if params is None:
            params = query
        return query.session.query(self.model, fargs={self.field.name: params})


############################################    MANY2MANY MANAGER

class ManyToManyRelatedManager(OneToManyRelatedManager):
    '''A specialized :class:`.OneToManyRelatedManager` for handling
    many-to-many relationships under the hood.

    When a model has a :class:`ManyToManyField`, instances
    of that model will have access to the related objects via a simple
    attribute of the model.'''
    def session_instance(self, name, value, session, **kwargs):
        if self.related_instance is None:
            raise ManyToManyError('Cannot use "%s" method from class' % name)
        elif not self.related_instance.pkvalue():
            raise ManyToManyError('Cannot use "%s" method on a non persistent '
                                  'instance.' % name)
        elif not isinstance(value, self.formodel):
            raise ManyToManyError(
                '%s is not an instance of %s' % (value, self.formodel._meta))
        elif not value.pkvalue():
            raise ManyToManyError('Cannot use "%s" a non persistent instance.'
                                  % name)
        kwargs.update({self.name_formodel: value,
                       self.name_relmodel: self.related_instance})
        return self.session(session), self.model(**kwargs)

    def add(self, value, session=None, **kwargs):
        '''Add ``value``, an instance of :attr:`formodel` to the
:attr:`through` model. This method can only be accessed by an instance of the
model for which this related manager is an attribute.'''
        s, instance = self.session_instance('add', value, session, **kwargs)
        return s.add(instance)

    def remove(self, value, session=None):
        '''Remove *value*, an instance of ``self.model`` from the set of
elements contained by the field.'''
        s, instance = self.session_instance('remove', value, session)
        # update state so that the instance does look persistent
        instance.get_state(iid=instance.pkvalue(), action='update')
        return s.delete(instance)

    def throughquery(self, session=None):
        '''Return a :class:`Query` on the ``throughmodel``, the model
used to hold the :ref:`many-to-many relationship <many-to-many>`.'''
        return super(ManyToManyRelatedManager, self).query(session)

    def query(self, session=None):
        # Return a query for the related model
        ids = self.throughquery(session).get_field(self.name_formodel)
        pkey = self.formodel.pk().name
        fargs = {pkey: ids}
        return self.session(session).query(self.formodel).filter(**fargs)


def makeManyToManyRelatedManager(formodel, name_relmodel, name_formodel):
    '''formodel is the model which the manager .'''

    class _ManyToManyRelatedManager(ManyToManyRelatedManager):
        pass

    _ManyToManyRelatedManager.formodel = formodel
    _ManyToManyRelatedManager.name_relmodel = name_relmodel
    _ManyToManyRelatedManager.name_formodel = name_formodel
    return _ManyToManyRelatedManager
