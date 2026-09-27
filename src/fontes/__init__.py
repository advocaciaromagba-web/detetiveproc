"""Fontes de descoberta que não passam pelo site do tribunal (seção 5, estratégias B e C).

Uma fonte descobre números de processo / publicações a partir de APIs públicas (DJEN,
DataJud) e alimenta o mesmo pipeline. Diferente de um adaptador de tribunal, ela não
faz ``obter_processo``; serve o eproc, onde a consulta pública exige verificação humana.
"""
