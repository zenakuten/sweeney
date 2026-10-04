class Probe extends Info;
function int F(optional out array<int> A)
{
    return A.Length;
}
function G()
{
    local array<int> L;
    F(L);
}
defaultproperties
{
}
