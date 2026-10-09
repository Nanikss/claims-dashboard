"""JSON API. Each handler: validate input -> run one module-level query -> jsonify."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from . import queries
from .db import get_db
from .filters import MAX_PER_PAGE, ValidationError, parse_filters, parse_int

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.errorhandler(ValidationError)
def bad_request(err: ValidationError):
    return jsonify(error=str(err)), 400


@bp.get("/kpis")
def kpis():
    return jsonify(queries.kpis(get_db(), parse_filters(request.args)))


@bp.get("/claims/monthly")
def claims_monthly():
    return jsonify(months=queries.monthly(get_db(), parse_filters(request.args)))


@bp.get("/loss-ratio")
def loss_ratio():
    return jsonify(queries.loss_ratio(get_db(), parse_filters(request.args)))


@bp.get("/claims")
def claims():
    filters = parse_filters(request.args)
    sort = request.args.get("sort", "loss_date")
    order = request.args.get("order", "desc").lower()
    if sort not in queries.SORT_COLUMNS:
        raise ValidationError(f"unknown sort {sort!r}; expected one of {', '.join(queries.SORT_COLUMNS)}")
    if order not in queries.SORT_ORDERS:
        raise ValidationError("order must be 'asc' or 'desc'")
    page = parse_int(request.args, "page", default=1, lo=1)
    per_page = parse_int(request.args, "per_page", default=25, lo=1, hi=MAX_PER_PAGE)
    return jsonify(queries.claims_page(get_db(), filters, sort=sort, order=order,
                                       page=page, per_page=per_page))
